#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""compose.py — 本地成片管线（画面轨）

把一份 project.json 渲染成无声成片：
  HTML 分镜 -> headless Chrome 截图 -> FFmpeg 分段渲染 -> concat 拼接

用法:
    python compose.py project.json            # 全量
    python compose.py project.json --only P3  # 只重渲指定段（调试用）
    FORCE=1 python compose.py project.json    # 忽略缓存全量重渲

依赖: ffmpeg/ffprobe/chrome(PIL 仅用于字幕条与标签)
"""
import os
import sys
import json
import shutil
import subprocess
from pathlib import Path

# ---------------------------------------------------------------- 环境探测

WIN_CANDIDATES = {
    "ffmpeg": [r"C:\Users\%s\AppData\Local\Microsoft\WinGet\Links\ffmpeg.exe"],
    "chrome": [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ],
    "regular_font": [r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\simhei.ttf"],
    "bold_font": [r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\msyh.ttc"],
}
MAC_CANDIDATES = {
    "chrome": [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    ],
    "regular_font": ["/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/Hiragino Sans GB.ttc"],
    "bold_font": ["/System/Library/Fonts/PingFang.ttc"],
}
# Linux（容器内）—— SoloHost / Docker 场景
LINUX_CANDIDATES = {
    "ffmpeg": ["/usr/bin/ffmpeg", "/usr/local/bin/ffmpeg"],
    "chrome": [
        "/usr/bin/chromium-headless-shell",
        "/usr/bin/chromium",
        "/usr/bin/chromium-browser",
        "/usr/bin/google-chrome",
        "/usr/bin/google-chrome-stable",
    ],
    "regular_font": [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansSC-Regular.otf",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ],
    "bold_font": [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansSC-Bold.otf",
        "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    ],
}


def _first_exist(paths):
    for p in paths:
        p = p.replace("%s", os.environ.get("USERNAME", ""))
        if os.path.exists(p):
            return p
    return None


def find_tools():
    if os.name == "nt":
        cand = WIN_CANDIDATES
    elif sys.platform.startswith("linux"):
        cand = LINUX_CANDIDATES
    else:
        cand = MAC_CANDIDATES
    ffmpeg = shutil.which("ffmpeg") or _first_exist(cand["ffmpeg"])
    ffprobe = shutil.which("ffprobe")
    if ffmpeg and not ffprobe:
        guess = str(Path(ffmpeg).with_name("ffprobe.exe" if os.name == "nt" else "ffprobe"))
        ffprobe = guess if os.path.exists(guess) else None
    # 容器内优先用 CHROME_BIN 环境变量（覆盖 headless-shell 等路径）
    chrome = (os.environ.get("CHROME_BIN")
               or shutil.which("chromium-headless-shell")
               or shutil.which("chromium")
               or shutil.which("chromium-browser")
               or shutil.which("google-chrome")
               or _first_exist(cand["chrome"]))
    if not (ffmpeg and ffprobe):
        sys.exit("找不到 ffmpeg / ffprobe，请先安装并加入 PATH")
    return {
        "ffmpeg": ffmpeg,
        "ffprobe": ffprobe,
        "chrome": chrome or sys.exit("找不到 Chrome 或 Edge，无法渲染 HTML 分镜"),
        "font": _first_exist(cand["regular_font"]),
        "font_bold": _first_exist(cand["bold_font"]),
    }


TOOLS = find_tools()


def probe_dur(p):
    out = subprocess.run(
        [TOOLS["ffprobe"], "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(p)],
        capture_output=True, text=True).stdout.strip()
    return float(out) if out else 0.0


def probe_wh(p):
    out = subprocess.run(
        [TOOLS["ffprobe"], "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=p=0:s=x", str(p)],
        capture_output=True, text=True).stdout.strip()
    try:
        w, h = out.split("x")[:2]
        return int(w), int(h)
    except Exception:
        return 0, 0


def uri(p):
    return "file:///" + str(p).replace("\\", "/")


# ---------------------------------------------------------------- 版式模板

GOLD = "#F2B705"
NAVY = "#0A1E3C"

OV_DEFAULT = ("linear-gradient(90deg,rgba(8,24,50,.86) 0%,rgba(8,24,50,.62) 50%,"
              "rgba(8,24,50,.20) 100%)")
OV_STRONG = ("linear-gradient(90deg,rgba(8,24,50,.92) 0%,rgba(8,24,50,.78) 52%,"
             "rgba(8,24,50,.46) 100%)")

PHOTO_TPL = """<!DOCTYPE html><html><head><meta charset="utf-8"><style>
*{margin:0;padding:0;box-sizing:border-box}
body{width:__W__px;height:__H__px;overflow:hidden;font-family:"Microsoft YaHei","PingFang SC",sans-serif;
 background-image:url('__BG__');background-size:cover;background-position:center}
.overlay{position:absolute;inset:0;background:__OV__}
.box{position:absolute;left:__PAD__px;top:50%;transform:translateY(-50%);max-width:__MAXW__px}
.kicker{font-size:__KS__px;color:__ACCENT__;letter-spacing:10px;margin-bottom:26px;opacity:.9}
.title{font-size:__TS__px;font-weight:800;color:__TITLE_COLOR__;letter-spacing:5px;line-height:1.22;
 text-shadow:0 6px 34px rgba(0,0,0,.55)}
.sub{font-size:__SS__px;color:#FFFFFF;letter-spacing:3px;margin-top:34px;text-shadow:0 4px 20px rgba(0,0,0,.6)}
.line{width:170px;height:8px;background:__ACCENT__;margin-top:42px;border-radius:4px}
</style></head><body><div class="overlay"></div><div class="box">
<div class="kicker">__KICKER__</div><div class="title">__TITLE__</div>
<div class="sub">__SUB__</div><div class="line"></div>
</div></body></html>"""

CARD_TPL = """<!DOCTYPE html><html><head><meta charset="utf-8"><style>
*{margin:0;padding:0;box-sizing:border-box}
body{width:__W__px;height:__H__px;overflow:hidden;font-family:"Microsoft YaHei","PingFang SC",sans-serif;
 background:radial-gradient(ellipse at 28% 18%, #16375f 0%, #0A1E3C 55%, #061226 100%);color:#fff}
.grid{position:absolute;inset:0;background-image:linear-gradient(rgba(255,255,255,.035) 1px,transparent 1px),
 linear-gradient(90deg,rgba(255,255,255,.035) 1px,transparent 1px);background-size:80px 80px}
.wrap{position:absolute;inset:0;padding:0 __PAD__px;display:flex;flex-direction:column;justify-content:center}
.kicker{font-size:__KS__px;color:__ACCENT__;letter-spacing:10px;margin-bottom:22px}
.h{font-size:__TS__px;font-weight:800;letter-spacing:4px}
.rule{width:170px;height:8px;background:__ACCENT__;margin:34px 0 46px;border-radius:4px}
.row{display:flex;gap:34px}
.cell{flex:1;background:rgba(255,255,255,.085);border:1px solid rgba(242,183,5,.42);border-radius:16px;
 padding:34px 32px;min-height:190px}
.num{font-size:34px;color:__ACCENT__;font-weight:700;letter-spacing:2px}
.ct{font-size:46px;font-weight:700;margin-top:14px;letter-spacing:2px}
.cd{font-size:28px;color:rgba(255,255,255,.72);margin-top:12px;letter-spacing:1px}
</style></head><body><div class="grid"></div><div class="wrap">
<div class="kicker">__KICKER__</div><div class="h">__TITLE__</div><div class="rule"></div>
<div class="row">__CELLS__</div>
</div></body></html>"""


def shoot(html, png, w, h, force=False):
    """headless Chrome 截图；已存在且非强制则复用"""
    hp = str(png).replace(".png", ".html")
    Path(hp).write_text(html, encoding="utf-8")
    if not force and Path(png).exists() and Path(png).stat().st_size > 20000:
        return True
    env = os.environ.copy()
    for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy"):
        env.pop(k, None)
    subprocess.run([TOOLS["chrome"], "--headless", "--disable-gpu", "--no-sandbox", "--hide-scrollbars",
                    "--force-device-scale-factor=1", f"--window-size={w},{h}",
                    f"--screenshot={png}", hp], capture_output=True, env=env, timeout=180)
    return Path(png).exists() and Path(png).stat().st_size > 20000


def photo_html(tr, cfg):
    W, H = cfg["width"], cfg["height"]
    scale = H / 1080.0
    ov = tr.get("overlay", "default")
    ov = {"default": OV_DEFAULT, "strong": OV_STRONG, "none": "none"}.get(ov, ov)
    accent = tr.get("accent", GOLD)
    tcolor = tr.get("title_color", accent)
    html = (PHOTO_TPL
            .replace("__W__", str(W)).replace("__H__", str(H))
            .replace("__BG__", uri(tr["src"]))
            .replace("__OV__", ov)
            .replace("__PAD__", str(int(110 * W / 1920)))
            .replace("__MAXW__", str(int(1320 * W / 1920)))
            .replace("__KS__", str(int(30 * scale)))
            .replace("__TS__", str(int(tr.get("title_size", 100) * scale)))
            .replace("__SS__", str(int(tr.get("sub_size", 42) * scale)))
            .replace("__ACCENT__", accent)
            .replace("__TITLE_COLOR__", tcolor)
            .replace("__KICKER__", tr.get("kicker", ""))
            .replace("__TITLE__", tr.get("title", ""))
            .replace("__SUB__", tr.get("sub", "")))
    return html


def card_html(tr, cfg):
    W, H = cfg["width"], cfg["height"]
    scale = H / 1080.0
    accent = tr.get("accent", GOLD)
    cells = "".join(
        f'<div class="cell"><div class="num">{n}</div><div class="ct">{t}</div>'
        f'<div class="cd">{d}</div></div>'
        for n, t, d in (tr.get("cells") or []))
    html = (CARD_TPL
            .replace("__W__", str(W)).replace("__H__", str(H))
            .replace("__PAD__", str(int(120 * W / 1920)))
            .replace("__KS__", str(int(30 * scale)))
            .replace("__TS__", str(int(tr.get("title_size", 82) * scale)))
            .replace("__ACCENT__", accent)
            .replace("__KICKER__", tr.get("kicker", ""))
            .replace("__TITLE__", tr.get("title", ""))
            .replace("__CELLS__", cells))
    return html


# ---------------------------------------------------------------- 字幕 / 标签

def make_sub_png(text, out, cfg):
    from PIL import Image, ImageDraw, ImageFont
    W, H = cfg["width"], cfg["height"]
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    size = int(44 * H / 1080)
    maxw = int(W * 0.80)
    while size > 26:
        f = ImageFont.truetype(TOOLS["font"], size)
        if f.getbbox(text)[2] <= maxw:
            break
        size -= 2
    f = ImageFont.truetype(TOOLS["font"], size)
    tw = f.getbbox(text)[2]
    bw, bh = tw + int(76 * W / 1920), int(78 * H / 1080)
    x0, y0 = (W - bw) // 2, int(H - 152 * H / 1080)
    d.rounded_rectangle([x0, y0, x0 + bw, y0 + bh], radius=12, fill=(4, 12, 28, 165))
    d.text((x0 + int(38 * W / 1920), y0 + int(14 * H / 1080)), text, font=f, fill=(255, 255, 255, 242))
    im.save(out)


def make_label_png(text, out, cfg):
    from PIL import Image, ImageDraw, ImageFont
    W, H = cfg["width"], cfg["height"]
    im = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    f = ImageFont.truetype(TOOLS["font_bold"], int(42 * H / 1080))
    tw = f.getbbox(text)[2]
    x, y, hh = int(70 * W / 1920), int(60 * H / 1080), int(78 * H / 1080)
    d.rounded_rectangle([x, y, x + tw + int(56 * W / 1920), y + hh], radius=10, fill=(4, 12, 28, 170))
    d.rectangle([x, y, x + int(8 * W / 1920), y + hh], fill=(242, 183, 5, 255))
    d.text((x + int(34 * W / 1920), y + int(18 * H / 1080)), text, font=f, fill=(255, 225, 120, 255))
    im.save(out)


# ---------------------------------------------------------------- 分段渲染

def encode_args(cfg):
    return ["-r", str(cfg["fps"]), "-c:v", "libx264", "-preset", cfg.get("preset", "medium"),
            "-crf", str(cfg.get("crf", 20)), "-profile:v", "high", "-level", "4.0",
            "-pix_fmt", "yuv420p", "-video_track_timescale", "15360"]


def audio_args():
    return ["-c:a", "aac", "-b:a", "160k", "-ar", "44100", "-ac", "2"]


def seg_still(tr, cfg, png, out):
    """静帧段：可选缓慢推近 + 首尾淡入淡出"""
    W, H = cfg["width"], cfg["height"]
    dur, fps = tr["dur"], cfg["fps"]
    zoom = tr.get("kenburns", True)
    scale = int(max(W, H) * 1.2)
    vf = f"scale={scale}:-2," if zoom else f"scale={W}:{H},"
    if zoom:
        frames = max(int(dur * fps), 1)
        amt = float(tr.get("kenburns_amount", 0.045))
        vf += (f"zoompan=z='1+{amt}*on/{frames}':d=1:"
               f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={fps},")
    vf += "setsar=1"
    fade_in = tr.get("fade_in", 0)
    fade_out = tr.get("fade_out", 0)
    if fade_in:
        vf += f",fade=t=in:st=0:d={fade_in}"
    if fade_out:
        vf += f",fade=t=out:st={max(dur - fade_out, 0)}:d={fade_out}"
    vf += ",format=yuv420p"
    cmd = [TOOLS["ffmpeg"], "-y", "-v", "error",
           "-loop", "1", "-framerate", str(fps), "-t", str(dur), "-i", str(png),
           "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
           "-vf", vf, "-map", "0:v", "-map", "1:a",
           "-t", str(dur), "-shortest"] + encode_args(cfg) + audio_args() + [str(out)]
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode == 0, r.stderr[-400:]


def seg_video(tr, cfg, out, tmp):
    """视频段：横屏铺满 / 竖屏模糊底居中；可选标签、烧录字幕、保留原声"""
    W, H, fps = cfg["width"], cfg["height"], cfg["fps"]
    src = str(tr["src"])
    sd = probe_dur(src)
    take = min(tr["dur"], sd - 0.05)
    if take < tr["dur"] - 0.2:
        print(f"    ! 源素材仅 {take:.2f}s < 目标 {tr['dur']}s，按源长度截断")

    fgpre = ""
    if tr.get("crop_top"):
        c = float(tr["crop_top"])
        fgpre += f"crop=iw:ih*{1 - c:.4f}:0:ih*{c:.4f},"
    if tr.get("brightness") is not None:
        fgpre += (f"eq=brightness={tr.get('brightness')}:contrast={tr.get('contrast', 1.0)}:"
                  f"saturation={tr.get('saturation', 1.0)},")

    sw, sh = probe_wh(src)
    layout = tr.get("layout", "auto")
    if layout == "auto":
        layout = "vertical" if sh > sw else "fill"

    inputs = ["-i", src]
    idx = 1
    lab_i = sub_i = None
    if tr.get("label"):
        lp = tmp / f"lab_{tr['id']}.png"
        make_label_png(tr["label"], str(lp), cfg)
        inputs += ["-loop", "1", "-i", str(lp)]
        lab_i, idx = idx, idx + 1
    if tr.get("subtitle", True) and tr.get("vo"):
        sp = tmp / f"sub_{tr['id']}.png"
        make_sub_png(tr["vo"], str(sp), cfg)
        inputs += ["-loop", "1", "-i", str(sp)]
        sub_i, idx = idx, idx + 1
    if tr.get("keep_audio"):
        aud_i = 0
    else:
        inputs += ["-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo"]
        aud_i, idx = idx, idx + 1

    ch = []
    if layout == "vertical":
        vh = int(tr.get("vertical_height", 1000) * H / 1080)
        ch.append("[0:v]split=2[bg][fg]")
        ch.append(f"[bg]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
                  "boxblur=38:4,eq=brightness=-0.06:saturation=0.95[bgb]")
        ch.append(f"[fg]{fgpre}scale=-2:{vh}:force_original_aspect_ratio=decrease,setsar=1[fgs]")
        ch.append("[bgb][fgs]overlay=(W-w)/2:(H-h)/2[base]")
    else:
        ch.append(f"[0:v]{fgpre}scale={W}:{H}:force_original_aspect_ratio=increase,"
                  f"crop={W}:{H},setsar=1[base]")

    last = "base"
    if lab_i is not None:
        hold = tr.get("label_hold", 2.5)
        ch.append(f"[{last}][{lab_i}:v]overlay=0:0:enable='lt(t,{hold})'[l1]")
        last = "l1"
    if sub_i is not None:
        ch.append(f"[{last}][{sub_i}:v]overlay=0:0[l2]")
        last = "l2"
    ch.append(f"[{last}]fps={fps},format=yuv420p,trim=duration={take},setpts=PTS-STARTPTS[v]")

    cmd = ([TOOLS["ffmpeg"], "-y", "-v", "error"] + inputs +
           ["-filter_complex", ";".join(ch), "-map", "[v]", "-map", f"{aud_i}:a",
            "-t", str(take), "-shortest"] + encode_args(cfg) + audio_args() + [str(out)])
    r = subprocess.run(cmd, capture_output=True, text=True)
    return r.returncode == 0, r.stderr[-400:], take


# ---------------------------------------------------------------- 主流程

def main():
    if len(sys.argv) < 2:
        sys.exit("用法: python compose.py project.json [--only ID]")
    cfg_path = Path(sys.argv[1]).resolve()
    root = cfg_path.parent
    only = None
    if "--only" in sys.argv:
        only = sys.argv[sys.argv.index("--only") + 1]

    cfg = json.loads(cfg_path.read_text(encoding="utf-8"))
    cfg.setdefault("width", 1920)
    cfg.setdefault("height", 1080)
    cfg.setdefault("fps", 30)
    build = root / cfg.get("build_dir", "build")
    tmp = build / "tmp"
    tmp.mkdir(parents=True, exist_ok=True)
    force = bool(os.environ.get("FORCE"))

    def ap(p):
        """素材相对路径按 project.json 所在目录解析"""
        p = Path(p)
        return p if p.is_absolute() else (root / p)

    tracks = cfg["tracks"]
    print(f"画布 {cfg['width']}x{cfg['height']}@{cfg['fps']}fps，共 {len(tracks)} 段，"
          f"总时长 {sum(t['dur'] for t in tracks):.2f}s")

    segs = []
    for tr in tracks:
        out = build / f"seg_{tr['id']}.mp4"
        if only and tr["id"] != only:
            segs.append(str(out))
            continue
        deps = []
        kind = tr["type"]
        if kind in ("photo", "card"):
            png = tmp / f"{tr['id']}.png"
            if kind == "photo":
                tr["src"] = str(ap(tr["src"]))
                html = photo_html(tr, cfg)
            else:
                html = card_html(tr, cfg)
            ok_shot = shoot(html, png, cfg["width"], cfg["height"], force=force)
            print(f"  {tr['id']} 截图: {'OK' if ok_shot else 'FAIL'}")
            deps = [png]
            if not force and out.exists() and out.stat().st_size > 10000 \
                    and out.stat().st_mtime > png.stat().st_mtime:
                print(f"  {tr['id']} 复用")
                segs.append(str(out))
                continue
            ok, err = seg_still(tr, cfg, png, out)
        else:
            tr["src"] = str(ap(tr["src"]))
            ok, err, take = seg_video(tr, cfg, out, tmp)
            print(f"  {tr['id']} {tr['dur']}s: {'OK' if ok else 'FAIL ' + err}")
            if ok:
                segs.append(str(out))
            continue
        print(f"  {tr['id']} {tr['dur']}s: {'OK' if ok else 'FAIL ' + err}")
        if ok:
            segs.append(str(out))

    missing = [s for s in segs if not Path(s).exists()]
    if missing:
        sys.exit(f"以下分段缺失，无法拼接: {missing}")

    lst = build / "list.txt"
    lst.write_text("".join(f"file '{s}'\n" for s in segs), encoding="utf-8")
    silent = build / cfg.get("silent_name", "silent.mp4")
    r = subprocess.run([TOOLS["ffmpeg"], "-y", "-v", "error", "-f", "concat", "-safe", "0",
                        "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(silent)],
                       capture_output=True, text=True)
    if r.returncode != 0:
        print("  无损拼接失败，回退重编码:", r.stderr[-200:])
        r = subprocess.run([TOOLS["ffmpeg"], "-y", "-v", "error", "-f", "concat", "-safe", "0",
                            "-i", str(lst)] + encode_args(cfg) + audio_args() + [str(silent)],
                           capture_output=True, text=True)
        if r.returncode != 0:
            sys.exit("拼接失败: " + r.stderr[-600:])

    total = sum(t["dur"] for t in tracks)
    print(f"\n无声轨: {silent}  {probe_dur(silent):.2f}s (目标 {total:.2f}s)")
    print("下一步: python voiceover.py " + cfg_path.name)


if __name__ == "__main__":
    main()
