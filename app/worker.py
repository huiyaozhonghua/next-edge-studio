#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
worker.py — 渲染 Worker

轮询 /data/work/job.json，拿到任务就跑管线，把进度写回 /data/work/status.json。
与 web 服务分离，因为渲染是长任务（一条 90 秒成片要几分钟），
不能占着 UI 端口（契约要求 UI 服务恰好一个端口）。

管线：
  compose.py   无声成片（HTML分镜 → Chromium → FFmpeg → concat）
  voiceover.py 旁白混入（逐句 TTS → atempo 校准 → loudnorm → adelay → amix）

TTS 引擎：
  piper  纯离线（容器内自带模型），默认
  edge   联网（edge-tts），音质更好但需要能直连
  none   不配音
"""
import os
import sys
import json
import time
import shutil
import signal
import subprocess
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
WORK_DIR = DATA_DIR / "work"
BUILD_DIR = WORK_DIR / "build"
OUT_DIR = WORK_DIR / "out"
JOB_FILE = WORK_DIR / "job.json"
STATUS_FILE = WORK_DIR / "status.json"
PIPER_MODEL_DIR = Path(os.environ.get("PIPER_MODEL_DIR", "/models"))

for d in (WORK_DIR, BUILD_DIR, OUT_DIR):
    d.mkdir(parents=True, exist_ok=True)

_running = False


def write_status(state, message="", progress=0, log=None, extra=None):
    st = {
        "state": state,
        "message": message,
        "progress": progress,
        "updated_at": time.time(),
        "log": list(log or [])[-40:],
    }
    if extra:
        st.update(extra)
    tmp = STATUS_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATUS_FILE)


def run_step(cmd, log, cwd=None, timeout=3600):
    """跑一个子进程，把输出实时追加到 log（log 是 list，就地修改并返回）"""
    log.append(f"$ {' '.join(str(c) for c in cmd)}")
    p = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE,
                         stderr=subprocess.STDOUT, text=True, encoding="utf-8",
                         errors="replace", bufsize=1)
    t0 = time.time()
    while True:
        line = p.stdout.readline()
        if line:
            log.append(line.rstrip())
        if p.poll() is not None:
            break
        if time.time() - t0 > timeout:
            p.kill()
            log.append(f"!! 超时 {timeout}s，已终止")
            return 124
    return p.returncode


def prepare_piper(project):
    """把 Piper 模型挂进 project 配置，并确认模型存在"""
    voice = project.setdefault("voice", {})
    engine = voice.get("engine", "piper")
    if engine != "piper":
        return engine
    name = voice.get("name", "zh_CN-huayan-medium")
    onnx = PIPER_MODEL_DIR / f"{name}.onnx"
    cfg = PIPER_MODEL_DIR / f"{name}.onnx.json"
    if not onnx.exists():
        # 退回容器内自带目录
        alt = APP_DIR / "models"
        onnx = alt / f"{name}.onnx"
        cfg = alt / f"{name}.onnx.json"
    if not onnx.exists():
        available = sorted(p.stem for p in PIPER_MODEL_DIR.glob("*.onnx"))
        raise FileNotFoundError(
            f"找不到 Piper 模型 {name}.onnx。"
            f"已挂载目录里可选：{available or '（无）'}")
    voice["model_path"] = str(onnx)
    voice["config_path"] = str(cfg) if cfg.exists() else ""
    return engine


def process(job, log):
    project = job["project"]
    # 素材路径统一：src 若是 assets/x.jpg，复制/软链到 work 下，让管线能相对寻址
    assets = DATA_DIR / "assets"
    for t in project.get("tracks", []):
        src = (t.get("src") or "").strip()
        if not src:
            continue
        name = Path(src).name
        s = assets / name
        if s.exists():
            dst = WORK_DIR / "assets" / name
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists() or dst.stat().st_size != s.stat().st_size:
                shutil.copy2(s, dst)
            t["src"] = f"assets/{name}"

    engine = prepare_piper(project)
    log.append(f"TTS 引擎: {engine}")
    if engine == "piper":
        log.append(f"Piper 模型: {project['voice'].get('model_path')}")

    # 写项目文件
    proj_path = WORK_DIR / "project.json"
    proj_path.write_text(json.dumps(project, ensure_ascii=False, indent=2), encoding="utf-8")

    # 清干净上次的中间产物
    if BUILD_DIR.exists():
        shutil.rmtree(BUILD_DIR, ignore_errors=True)
    BUILD_DIR.mkdir(parents=True, exist_ok=True)

    # 1) 无声成片
    write_status("running", "渲染画面轨…", 15, log)
    rc = run_step([sys.executable, str(APP_DIR / "compose.py"), str(proj_path)],
                  log, cwd=WORK_DIR)
    if rc != 0:
        raise RuntimeError("画面轨渲染失败（compose.py）")

    # 2) 旁白混入
    if engine != "none" and any((t.get("vo") or "").strip() for t in project.get("tracks", [])):
        write_status("running", "生成旁白并混音…", 60, log)
        rc = run_step([sys.executable, str(APP_DIR / "voiceover.py"), str(proj_path)],
                      log, cwd=WORK_DIR)
        if rc != 0:
            raise RuntimeError("旁白混音失败（voiceover.py）")
    else:
        # 无旁白 → 直接把无声成片作为成品
        silent = BUILD_DIR / project.get("silent_name", "silent.mp4")
        if silent.exists():
            shutil.copy2(silent, OUT_DIR / "final.mp4")

    final = OUT_DIR / "final.mp4"
    if not final.exists():
        raise RuntimeError("渲染结束但没有产出成片")
    return {
        "output": "final.mp4",
        "size": final.stat().st_size,
        "duration": probe_duration(final),
    }


def probe_duration(f):
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "format=duration", "-of", "default=nw=1:nk=1", str(f)],
                           capture_output=True, text=True, timeout=30)
        return round(float(r.stdout.strip()), 2)
    except Exception:
        return None


def handle(job):
    global _running
    _running = True
    log = []
    write_status("running", "开始渲染…", 5, log, {"job_id": job.get("id")})
    try:
        info = process(job, log)
        write_status("done", f"完成 · {info['duration']}s · {info['size'] / 1024 / 1024:.1f}MB",
                     100, log, {"job_id": job.get("id"), "output": info})
    except Exception as e:
        write_status("failed", f"{type(e).__name__}: {e}", 0, log, {"job_id": job.get("id")})
    finally:
        _running = False
        try:
            JOB_FILE.unlink()
        except Exception:
            pass


def main():
    print("[worker] 等待渲染任务…", flush=True)
    last_mtime = 0
    while True:
        try:
            if JOB_FILE.exists():
                mtime = JOB_FILE.stat().st_mtime
                if mtime != last_mtime and not _running:
                    last_mtime = mtime
                    try:
                        job = json.loads(JOB_FILE.read_text(encoding="utf-8"))
                    except Exception as e:
                        write_status("failed", f"任务文件损坏: {e}")
                        continue
                    handle(job)
        except Exception as e:
            sys.stderr.write(f"[worker] {type(e).__name__}: {e}\n")
        time.sleep(1.0)


if __name__ == "__main__":
    main()
