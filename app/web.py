#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
web.py — Next Edge Studio 的 Web 服务（零第三方依赖）

职责：
  - 提供单页 UI
  - 素材上传 / 列表 / 删除
  - 时间线配置读写（project.json）
  - 触发渲染（写 job.json 交给 worker）
  - 查询渲染进度（读 status.json）
  - 成片预览与下载

数据布局（/data 是 SoloHost 的 named volume）：
  /data/assets/          上传的素材
  /data/work/project.json  时间线配置
  /data/work/job.json     渲染任务（web 写，worker 读）
  /data/work/status.json  渲染状态（worker 写，web 读）
  /data/work/build/       渲染中间产物
  /data/work/out/         最终成片

注意：SoloHost 会把应用嵌在 webview 里，访问来源是 127.0.0.1:HOST_PORT，
而容器内监听 localhost:CONTAINER —— 两者是不同 origin。所有响应都不设
X-Frame-Options，且 CORS 放开，避免 origin 拒绝。
"""
import os
import re
import sys
import json
import time
import shutil
import mimetypes
import subprocess
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

DATA_DIR = Path(os.environ.get("DATA_DIR", "/data"))
ASSETS_DIR = DATA_DIR / "assets"
WORK_DIR = DATA_DIR / "work"
BUILD_DIR = WORK_DIR / "build"
OUT_DIR = WORK_DIR / "out"
PROJECT_FILE = WORK_DIR / "project.json"
JOB_FILE = WORK_DIR / "job.json"
STATUS_FILE = WORK_DIR / "status.json"
STATIC_DIR = Path(__file__).resolve().parent / "static"

PORT = int(os.environ.get("PORT", "8080"))
MAX_UPLOAD = int(os.environ.get("MAX_UPLOAD_MB", "512")) * 1024 * 1024

ALLOWED_EXT = {".jpg", ".jpeg", ".png", ".webp", ".gif", ".bmp",
               ".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}

for d in (ASSETS_DIR, WORK_DIR, BUILD_DIR, OUT_DIR):
    d.mkdir(parents=True, exist_ok=True)


# ---------------------------------------------------------------- 默认配置

def default_project():
    return {
        "width": 1080,
        "height": 1920,
        "fps": 30,
        "build_dir": "build",
        "silent_name": "silent.mp4",
        "final_output": "out/final.mp4",
        "voice": {
            "engine": os.environ.get("TTS_ENGINE", "piper"),
            "name": os.environ.get("PIPER_VOICE", "zh_CN-huayan-medium"),
            "rate": "+0%",
            "gain_db": 2.0,
            "target_lufs": -16,
        },
        "tracks": [
            {
                "id": "P1", "type": "photo", "src": "", "dur": 4.0,
                "kicker": "NEXT EDGE STUDIO", "title": "你的第一段",
                "sub": "改文案、换素材，点右边重渲", "accent": "#F2B705",
                "overlay": "default", "fade_in": 0, "fade_out": 0,
                "vo": "旁白写在这里，会自动配上字幕。",
            },
        ],
    }


def load_project():
    if PROJECT_FILE.exists():
        try:
            return json.loads(PROJECT_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    p = default_project()
    save_project(p)
    return p


def save_project(p):
    tmp = PROJECT_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(p, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(PROJECT_FILE)


def load_status():
    if STATUS_FILE.exists():
        try:
            return json.loads(STATUS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"state": "idle", "message": "等待新任务", "progress": 0, "log": []}


# ---------------------------------------------------------------- HTTP 处理

class Handler(BaseHTTPRequestHandler):
    server_version = "NextEdgeStudio/1.0"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        sys.stderr.write("[web] " + (fmt % args) + "\n")

    # ---- 基础响应 ----
    def _cors(self):
        # 允许跨源：SoloHost 以不同 origin 嵌入
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send(self, code, body=b"", ctype="application/octet-stream", extra=None):
        if isinstance(body, str):
            body = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # 不设 X-Frame-Options —— Pi Desktop 的 App 标签页是顶层 webview 导航
        self._cors()
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, code=200):
        self._send(code, json.dumps(obj, ensure_ascii=False), "application/json; charset=utf-8")

    def _err(self, code, msg):
        self._json({"ok": False, "error": msg}, code)

    def do_OPTIONS(self):
        self._send(204, b"", "text/plain")

    # ---- 路由 ----
    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        p = u.path
        try:
            if p in ("/", "/index.html"):
                return self._serve_static("index.html")
            if p == "/api/state":
                return self._api_state()
            if p == "/api/assets":
                return self._api_assets()
            if p == "/api/project":
                return self._json({"ok": True, "project": load_project()})
            if p == "/api/status":
                return self._json({"ok": True, "status": load_status()})
            if p.startswith("/api/output/"):
                return self._serve_output(p[len("/api/output/"):])
            if p.startswith("/assets/"):
                return self._serve_asset(p[len("/assets/"):])
            if p.startswith("/static/"):
                return self._serve_static(p[len("/static/"):])
            return self._err(404, "not found")
        except Exception as e:
            return self._err(500, f"{type(e).__name__}: {e}")

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        p = u.path
        try:
            if p == "/api/upload":
                return self._api_upload()
            if p == "/api/project":
                return self._api_save_project()
            if p == "/api/render":
                return self._api_render()
            if p == "/api/reset":
                return self._api_reset()
            return self._err(404, "not found")
        except Exception as e:
            return self._err(500, f"{type(e).__name__}: {e}")

    def do_DELETE(self):
        u = urllib.parse.urlparse(self.path)
        if u.path.startswith("/api/assets/"):
            name = urllib.parse.unquote(u.path[len("/api/assets/"):])
            f = (ASSETS_DIR / name).resolve()
            try:
                if f.is_file() and str(f).startswith(str(ASSETS_DIR.resolve())):
                    f.unlink()
                    return self._json({"ok": True})
            except Exception as e:
                return self._err(500, str(e))
        return self._err(404, "not found")

    # ---- 具体接口 ----
    def _api_state(self):
        st = load_status()
        return self._json({
            "ok": True,
            "app": "Next Edge Studio",
            "voice_engine": os.environ.get("TTS_ENGINE", "piper"),
            "piper_voice": os.environ.get("PIPER_VOICE", "zh_CN-huayan-medium"),
            "status": st,
            "has_output": (OUT_DIR / "final.mp4").exists(),
        })

    def _api_assets(self):
        items = []
        for f in sorted(ASSETS_DIR.iterdir()):
            if not f.is_file():
                continue
            items.append({
                "name": f.name,
                "size": f.stat().st_size,
                "ext": f.suffix.lower(),
                "kind": "video" if f.suffix.lower() in {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"} else "image",
            })
        return self._json({"ok": True, "assets": items})

    def _api_upload(self):
        ctype = self.headers.get("Content-Type", "")
        if "multipart/form-data" not in ctype:
            return self._err(400, "需要 multipart/form-data")
        m = re.search(r"boundary=([^;]+)", ctype)
        if not m:
            return self._err(400, "缺少 boundary")
        boundary = m.group(1).strip('"').encode()
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return self._err(400, "空请求体")
        if length > MAX_UPLOAD:
            return self._err(413, f"请求体超过 {MAX_UPLOAD // 1024 // 1024}MB 限制")

        raw = self.rfile.read(length)
        saved, skipped = [], []
        delim = b"--" + boundary
        for part in raw.split(delim):
            if not part or part in (b"--\r\n", b"--", b"\r\n"):
                continue
            head, _, body = part.partition(b"\r\n\r\n")
            if not head:
                continue
            body = body[:-2] if body.endswith(b"\r\n") else body
            head_s = head.decode("utf-8", "replace")
            fn_m = re.search(r'filename="([^"]*)"', head_s)
            if not fn_m:
                continue
            fname = os.path.basename(urllib.parse.unquote(fn_m.group(1)))
            if not fname:
                continue
            ext = Path(fname).suffix.lower()
            if ext not in ALLOWED_EXT:
                skipped.append({"name": fname, "reason": f"不支持的类型 {ext}"})
                continue
            safe = re.sub(r"[^\w.\-一-鿿]", "_", fname) or f"asset{ext}"
            target = ASSETS_DIR / safe
            i = 1
            while target.exists():
                target = ASSETS_DIR / f"{Path(safe).stem}_{i}{ext}"
                i += 1
            target.write_bytes(body)
            saved.append({"name": target.name, "size": target.stat().st_size, "ext": ext})
        return self._json({"ok": True, "saved": saved, "skipped": skipped})

    def _api_save_project(self):
        n = int(self.headers.get("Content-Length", "0"))
        if n <= 0:
            return self._err(400, "空请求体")
        try:
            p = json.loads(self.rfile.read(n).decode("utf-8"))
        except Exception as e:
            return self._err(400, f"JSON 解析失败: {e}")
        if not isinstance(p, dict) or "tracks" not in p:
            return self._err(400, "缺少 tracks")
        # 校验素材确实存在
        for t in p["tracks"]:
            src = (t.get("src") or "").strip()
            if src and not (WORK_DIR / src).exists() and not (ASSETS_DIR / Path(src).name).exists():
                return self._err(400, f"素材不存在: {src}")
        save_project(p)
        return self._json({"ok": True})

    def _api_render(self):
        st = load_status()
        if st.get("state") == "running":
            return self._err(409, "已有渲染任务在跑")
        job = {
            "id": time.strftime("%Y%m%d-%H%M%S"),
            "queued_at": time.time(),
            "project": load_project(),
        }
        JOB_FILE.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        # 清理旧产物
        for f in OUT_DIR.glob("*"):
            try:
                f.unlink()
            except Exception:
                pass
        return self._json({"ok": True, "job_id": job["id"]})

    def _api_reset(self):
        p = default_project()
        save_project(p)
        return self._json({"ok": True, "project": p})

    # ---- 静态与产物 ----
    def _serve_static(self, rel):
        f = (STATIC_DIR / rel).resolve()
        if not f.is_file() or not str(f).startswith(str(STATIC_DIR.resolve())):
            return self._err(404, "not found")
        ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype == "application/javascript":
            ctype += "; charset=utf-8"
        return self._send(200, f.read_bytes(), ctype, {"Cache-Control": "no-cache"})

    def _serve_asset(self, rel):
        name = os.path.basename(urllib.parse.unquote(rel))
        f = (ASSETS_DIR / name).resolve()
        if not f.is_file() or not str(f).startswith(str(ASSETS_DIR.resolve())):
            return self._err(404, "not found")
        ctype = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
        return self._send(200, f.read_bytes(), ctype, {"Cache-Control": "no-cache"})

    def _serve_output(self, rel):
        name = os.path.basename(urllib.parse.unquote(rel))
        f = (OUT_DIR / name).resolve()
        if not f.is_file() or not str(f).startswith(str(OUT_DIR.resolve())):
            return self._err(404, "not found")
        ctype = mimetypes.guess_type(str(f))[0] or "video/mp4"
        # 支持 Range 请求，让浏览器能拖动进度条
        rng = self.headers.get("Range")
        if rng and rng.startswith("bytes="):
            m = re.match(r"bytes=(\d*)-(\d*)", rng)
            total = f.stat().st_size
            start = int(m.group(1)) if m.group(1) else 0
            end = int(m.group(2)) if m.group(2) else total - 1
            end = min(end, total - 1)
            if start > end:
                return self._send(416, b"", ctype)
            length = end - start + 1
            with f.open("rb") as fh:
                fh.seek(start)
                data = fh.read(length)
            return self._send(206, data, ctype, {
                "Content-Range": f"bytes {start}-{end}/{total}",
                "Accept-Ranges": "bytes",
            })
        return self._send(200, f.read_bytes(), ctype, {"Accept-Ranges": "bytes"})


def main():
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    srv.daemon_threads = True
    print(f"[web] Next Edge Studio listening on 0.0.0.0:{PORT} (data={DATA_DIR})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
