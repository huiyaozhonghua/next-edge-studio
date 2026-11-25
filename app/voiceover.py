#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""voiceover.py — 本地成片管线（声音轨）

读取同一份 project.json，按时间线生成旁白并混入无声成片：
  逐句 TTS -> 时长校准(atempo) -> 响度统一(loudnorm) -> 按段起点对齐(adelay) -> amix

用法:
    python voiceover.py project.json              # 生成旁白并出成片
    python voiceover.py project.json --remix      # 跳过 TTS，只重混
    python voiceover.py project.json --probe      # 只打印时长校准报告

依赖: edge-tts (pip install edge-tts)，需要直连网络
"""
import os
import re
import sys
import json
import shutil
import hashlib
import subprocess
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from compose import TOOLS, probe_dur  # 复用环境探测

FFMPEG, FFPROBE = TOOLS["ffmpeg"], TOOLS["ffprobe"]

STRIP_PROXY = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
               "ALL_PROXY", "all_proxy", "NO_PROXY", "no_proxy")


def clean_env():
    env = os.environ.copy()
    for k in STRIP_PROXY:
        env.pop(k, None)
    return env


def tts_batch(items, out_dir):
    """items: [(id, text, name, rate)] —— 一次进程内批量合成，减少启动开销"""
    gen = Path(out_dir) / "_gen.py"
    gen.write_text(
        "import asyncio, edge_tts, os\n"
        f"ITEMS = {items!r}\n"
        f"OUT = r'{out_dir}'\n"
        "async def main():\n"
        "    for sid, text, name, rate in ITEMS:\n"
        "        await edge_tts.Communicate(text, name, rate=rate).save(\n"
        "            os.path.join(OUT, sid + '.mp3'))\n"
        "        print(sid, 'OK', flush=True)\n"
        "asyncio.run(main())\n",
        encoding="utf-8")
    r = subprocess.run([sys.executable, str(gen)], capture_output=True, text=True,
                       env=clean_env(), timeout=900)
    return r.returncode == 0, (r.stdout or "") + (r.stderr or "")


def tts_batch_piper(items, out_dir, model_path, config_path, length_scale=1.0):
    """Piper 批量合成 —— 纯离线，无需网络。

    items: [(id, text, name, rate)]
    rate 形如 "+4%" / "-2%" —— Piper 用 length_scale 控制语速（>1 更慢）。
    """
    gen = Path(out_dir) / "_gen_piper.py"
    gen.write_text(
        "import os, wave\n"
        "from piper import PiperVoice, SynthesisConfig\n"
        f"ITEMS = {items!r}\n"
        f"OUT = r'{out_dir}'\n"
        f"MODEL = r'{model_path}'\n"
        f"CONFIG = r'{config_path}'\n"
        f"LS = {length_scale!r}\n"
        "voice = PiperVoice.load(MODEL, config_path=CONFIG if CONFIG else None)\n"
        "for sid, text, name, rate in ITEMS:\n"
        "    ls = LS\n"
        "    if rate:\n"
        "        try:\n"
        "            pct = float(str(rate).replace('%','').replace('+',''))\n"
        "            ls = LS * (1.0 - pct / 100.0)\n"
        "        except Exception:\n"
        "            pass\n"
        "    chunks = list(voice.synthesize(text, SynthesisConfig(length_scale=max(0.5, ls))))\n"
        "    audio = b''.join(c.audio_int16_bytes for c in chunks)\n"
        "    with wave.open(os.path.join(OUT, sid + '.wav'), 'wb') as w:\n"
        "        w.setnchannels(1); w.setsampwidth(2)\n"
        "        w.setframerate(chunks[0].sample_rate)\n"
        "        w.writeframes(audio)\n"
        "    print(sid, 'OK', flush=True)\n",
        encoding="utf-8")
    r = subprocess.run([sys.executable, str(gen)], capture_output=True, text=True,
                       env=clean_env(), timeout=900)
    return r.returncode == 0, (r.stdout or "") + (r.stderr or "")


def main():
    if len(sys.argv) < 2:
        sys.exit("用法: python voiceover.py project.json [--remix|--probe]")
    cfg_path = Path(sys.argv[1]).resolve()
    root = cfg_path.parent
    remix_only = "--remix" in sys.argv
    probe_only = "--probe" in sys.argv

    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    tracks = cfg["tracks"]
    total = sum(t["dur"] for t in tracks)
    build = root / cfg.get("build_dir", "build")
    vo_dir = build / "vo"
    vo_dir.mkdir(parents=True, exist_ok=True)
    silent = build / cfg.get("silent_name", "silent.mp4")
    final = root / cfg.get("final_output", "final.mp4")

    if not silent.exists():
        sys.exit(f"缺少无声成片 {silent}，请先运行 compose.py")

    voice = cfg.get("voice") or {}
    engine = voice.get("engine", "edge")
    vname = voice.get("name", "zh-CN-YunyangNeural")
    vrate = voice.get("rate", "+4%")
    gain_db = float(voice.get("gain_db", 0))
    lufs = float(voice.get("target_lufs", -16))

    # 1) 时间轴：算出每段起点
    starts, acc = {}, 0.0
    for t in tracks:
        starts[t["id"]] = acc
        acc += t["dur"]

    # 2) 需要配音的段
    jobs = [t for t in tracks if (t.get("vo") or "").strip()]
    print(f"共 {len(tracks)} 段 / {total:.2f}s，其中 {len(jobs)} 段有旁白")

    cache_file = vo_dir / "cache.json"
    cache = {}
    if cache_file.exists():
        try:
            cache = json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            cache = {}

    if not remix_only and engine in ("edge", "piper"):
        ext = "wav" if engine == "piper" else "mp3"
        todo = []
        for t in jobs:
            key = hashlib.md5(f"{t['vo']}|{vname}|{vrate}|{engine}".encode()).hexdigest()[:12]
            src_raw = vo_dir / f"{t['id']}.{ext}"
            if cache.get(t["id"], {}).get("key") == key and src_raw.exists() and src_raw.stat().st_size > 2000:
                continue
            todo.append((t["id"], t["vo"], vname, vrate))
            cache[t["id"]] = {"key": key}
        if todo:
            print(f"  合成 {len(todo)} 句旁白（{engine} · {vname} {vrate}）…")
            if engine == "piper":
                model = voice.get("model_path", "")
                cfgp = voice.get("config_path", "")
                if not model or not Path(model).exists():
                    sys.exit(f"Piper 模型不存在: {model}")
                ok, log = tts_batch_piper(todo, str(vo_dir), model, cfgp)
            else:
                ok, log = tts_batch(todo, str(vo_dir))
            if not ok:
                hint = ("检查模型是否挂载到 /models" if engine == "piper"
                        else "检查网络是否可直连 speech.platform.bing.com")
                sys.exit(f"TTS 失败（{hint}）:\n" + log[-800:])
            print("  " + " ".join(x for x in log.split() if x == "OK" or x.endswith("OK")))
        else:
            print("  旁白均已缓存，跳过合成")
        cache_file.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")

    # 3) 时长校准 + 统一响度
    print("\n时长校准：")
    src_ext = "wav" if engine == "piper" else "mp3"
    norm = {}
    for t in jobs:
        src = vo_dir / f"{t['id']}.{src_ext}"
        if not src.exists():
            print(f"  {t['id']:6s} 缺音频，跳过")
            continue
        d = probe_dur(src)
        budget = max(t["dur"] - 0.25, 0.5)
        tempo = min(d / budget, 1.32) if d > budget else 1.0
        # 校准产物用独立文件名：Piper 的原始输出就是 .wav，同名会让 FFmpeg
        # 报 "Output same as Input" 而静默失败（表现为成片没有旁白）
        out = vo_dir / f"{t['id']}.norm.wav"
        af = (f"atempo={tempo:.4f}," if tempo > 1.001 else "")
        af += f"loudnorm=I={lufs}:TP=-1.5:LRA=9,aformat=sample_rates=44100:channel_layouts=mono"
        r = subprocess.run([FFMPEG, "-y", "-v", "error", "-i", str(src), "-filter:a", af, str(out)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print(f"  {t['id']:6s} 处理失败 {r.stderr[-200:]}")
            continue
        fd = probe_dur(out)
        flag = "  << 仍超段长" if fd > t["dur"] else ""
        print(f"  {t['id']:6s} {d:5.2f}s -> {fd:5.2f}s / 段 {t['dur']:4.1f}s  tempo {tempo:.2f}{flag}")
        norm[t["id"]] = out

    if probe_only:
        return

    # 4) 混音
    order = [t for t in jobs if t["id"] in norm]
    inputs = [str(silent)] + [str(norm[t["id"]]) for t in order]
    cmd = [FFMPEG, "-y", "-v", "error"]
    for p in inputs:
        cmd += ["-i", p]
    parts = ["[0:a]aformat=sample_rates=44100:channel_layouts=stereo[base]"]
    for i, t in enumerate(order, start=1):
        ms = int(starts[t["id"]] * 1000)
        parts.append(f"[{i}:a]adelay={ms}|{ms},aformat=sample_rates=44100:channel_layouts=mono,"
                     f"volume=1.0[a{i}]")
    n = len(order) + 1
    mix = "[base]" + "".join(f"[a{i}]" for i in range(1, len(order) + 1))
    tail = f"amix=inputs={n}:duration=first:dropout_transition=0:normalize=0"
    if gain_db:
        tail += f",volume={gain_db}dB"
    tail += ",alimiter=limit=0.95[aout]"
    parts.append(mix + tail)

    cmd += ["-filter_complex", ";".join(parts), "-map", "0:v", "-map", "[aout]",
            "-t", f"{total:.3f}", "-c:v", "copy", "-c:a", "aac", "-b:a", "160k",
            "-ar", "44100", "-movflags", "+faststart", str(final)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        sys.exit("混音失败: " + r.stderr[-1000:])

    print(f"\n完成: {final}")
    print(f"大小 {final.stat().st_size / 1024 / 1024:.1f} MB, 时长 {probe_dur(final):.2f}s")

    lum = subprocess.run([FFMPEG, "-v", "info", "-i", str(final), "-af", "ebur128=peak=true",
                          "-f", "null", "-"], capture_output=True, text=True).stderr
    I = re.findall(r"I:\s+(-?\d+\.\d+) LUFS", lum)
    TP = re.findall(r"Peak:\s+(-?\d+\.\d+) dBFS", lum)
    print(f"响度: {I[-1] if I else '?'} LUFS | TruePeak {TP[-1] if TP else '?'} dBFS")
    print("建议 -14 ~ -16 LUFS；偏低/偏高就调 project.json 里的 voice.gain_db 后 --remix")


if __name__ == "__main__":
    main()
