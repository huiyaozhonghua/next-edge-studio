# Next Edge Studio — 容器镜像（Next Edge 的 SoloHost App）
#
# 构建（本网络阻断明文 HTTP 的 debian 源，且需走代理）：
#   HTTP_PROXY=http://127.0.0.1:17891 HTTPS_PROXY=http://127.0.0.1:17891 \
#   docker build -t ghcr.io/zjh18981927828/next-edge-studio:1.0.0 .
#
# 体积参考（实测）：
#   全量 chromium + 全套 CJK 字体        1.58 GB
#   headless-shell + 只留 Regular/Bold   1.22 GB
#   + Piper 中文模型 (63MB)               1.29 GB
#   对照：官方示例 Hermes Agent 镜像       895 MB
#
# 依赖实测：
#   ffmpeg 7.1.5 / chromium-headless-shell / 中文字体 20 个 /
#   Pillow 13.0.0 / piper-tts 1.8.0 (onnxruntime) / zh_CN-huayan-medium 模型

FROM python:3.11-slim

# --- apt 走 HTTPS（本网络阻断 deb.debian.org 的明文 HTTP）---
RUN sed -i 's|http://deb.debian.org|https://deb.debian.org|g; s|http://security.debian.org|https://security.debian.org|g' \
        /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's|http://deb.debian.org|https://deb.debian.org|g' /etc/apt/sources.list

# --- 运行时依赖 ---
RUN apt-get update && apt-get install -y --no-install-recommends \
      ffmpeg \
      chromium-headless-shell \
      fonts-noto-cjk \
      fontconfig \
      ca-certificates \
 && rm -rf /var/lib/apt/lists/* \
 && rm -rf /var/cache/apt/*

# --- 中文字体瘦身：只留 Regular + Bold（303MB → ~60MB）---
RUN fc-list :lang=zh file | grep -oE '/usr/share/fonts/[^:]+' | sort -u | \
    grep -vE 'NotoSansCJK-(Regular|Bold)\.ttc' | while read f; do rm -f "$f"; done; \
    fc-cache -f >/dev/null 2>&1

# --- Python 依赖（Piper 含 onnxruntime 78M + numpy 73M，是体积大头）---
RUN pip install --no-cache-dir pillow piper-tts \
 && rm -rf /root/.cache/pip

# --- 应用代码 + 离线中文语音模型 ---
WORKDIR /app
COPY app/ /app/

# --- Piper 中文语音模型 ---
# 模型不进 git 历史（63MB），构建时从 HuggingFace 下载。
# 走 build-arg 传入是为了让 CI 与本地构建都能覆盖来源。
ARG PIPER_MODEL_URL=https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx
ARG PIPER_CONFIG_URL=https://huggingface.co/rhasspy/piper-voices/resolve/main/zh/zh_CN/huayan/medium/zh_CN-huayan-medium.onnx.json

RUN mkdir -p /app/models && \
    (curl -fsSL --retry 3 -o /app/models/zh_CN-huayan-medium.onnx "$PIPER_MODEL_URL" \
     && curl -fsSL --retry 3 -o /app/models/zh_CN-huayan-medium.onnx.json "$PIPER_CONFIG_URL") || \
    echo "⚠️ 模型下载失败 —— 本地构建请手动放置到 app/models/"

# --- 自检 ---
RUN echo "ffmpeg:  $(ffmpeg -version 2>/dev/null | head -1 | cut -c1-30)" && \
    echo "chrome:  $(which chromium-headless-shell)" && \
    echo "zhfonts: $(fc-list :lang=zh | wc -l)" && \
    echo "piper:   $(ls /app/models/*.onnx 2>/dev/null | head -1 | xargs -r basename)" && \
    python3 -c "import PIL, piper; print('pillow+piper: ok')" && \
    python3 -m py_compile /app/web.py /app/worker.py /app/compose.py /app/voiceover.py && \
    echo "python syntax: ok"

# --- 运行时 ---
ENV PYTHONUNBUFFERED=1 \
    PORT=8080 \
    DATA_DIR=/data \
    PIPER_MODEL_DIR=/app/models \
    CHROME_BIN=/usr/bin/chromium-headless-shell \
    TTS_ENGINE=piper \
    PIPER_VOICE=zh_CN-huayan-medium \
    TZ=Asia/Shanghai

EXPOSE 8080
VOLUME ["/data"]

CMD ["python3", "/app/web.py"]
