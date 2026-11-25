# Next Edge Studio — 本地成片工作台

SoloHost App。把一堆照片和手机竖屏视频，变成一条带**中文旁白**和**烧录字幕**的成片 ——
全程在你自己的容器里跑，**素材一个字节都不外传**。

> 这是 Next Edge 的第二个参赛作品（第一个是 Next Edge Report 参赛作战台）。
> 两者技术栈、目标用户、分发渠道完全不同，不构成重复提交。

---

## 为什么做这个

- **Pi 节点操作者要的就是"东西在我机器上"** —— 这是 SoloHost 存在的理由
- **中文是空白** —— 官方 SoloHost 三个示例（OpenClaw / Hermes / Grafana）**全都不做中文**
- **离线可用** —— Piper 中文语音模型内置在镜像里，不需要联网、不需要 API Key
- **精确控时** —— 时间线误差 0.02 秒（实测 14.02s / 目标 14.00s）

---

## 架构

```
Pi Desktop (嵌入 webview)
   │  127.0.0.1:18790
   ▼
┌─────────────────────────────┐
│  web (容器)                  │  ← pi.ui.primary
│  · 单页 UI                  │
│  · 素材上传 / 时间线编辑      │
│  · 触发渲染 / 进度 / 预览下载  │
└──────────┬──────────────────┘
           │ named volume /data
┌──────────▼──────────────────┐
│  worker (容器, 同一镜像)      │  ← command: worker.py
│  · 轮询 job.json             │
│  · compose.py → 无声成片      │
│  · voiceover.py → 旁白混入    │
└─────────────────────────────┘
```

**为什么拆两个服务**：渲染是长任务（一条 14 秒成片约 30–60 秒），不能占着 UI 端口
（SoloHost 契约要求 UI 服务恰好发布一个端口）。

---

## 管线技术要点

### 画面轨（compose.py）
```
HTML 分镜 → Chromium headless 截图 → FFmpeg 分段渲染 → concat demuxer 无损拼接
```
- 精确控时：分段独立渲染 + concat，**不用 xfade**（xfade 会按最短输入截断时长）
- 竖屏素材自动"模糊底铺满 + 居中"，高度取画布 92%
- 卡片式纯文字段零素材风险

### 声音轨（voiceover.py）
```
逐句 TTS → atempo 时长校准 → loudnorm 响度统一 → adelay 按段起点对齐 → amix 混音
```
- 校准产物用独立文件名（`.norm.wav`）—— 与 Piper 原始输出同名会让 FFmpeg
  报 "Output same as Input" 而**静默失败**（表现为成片没有旁白但日志无明显报错）
- 混音加 `normalize=0`，否则 20 路混音后人声会小到听不见
- 实测响度 **-14.2 LUFS / TruePeak -2.0 dBFS**（在建议区间内）

### TTS 引擎
| 引擎 | 网络 | 音质 | 说明 |
|---|---|---|---|
| **piper**（默认） | 纯离线 | 中 | 内置 `zh_CN-huayan-medium` 模型（63MB） |
| edge | 需要 | 高 | edge-tts，需能直连 `speech.platform.bing.com` |
| none | — | — | 不配音 |

---

## 开发

### 构建镜像
```bash
cd solohost-studio
HTTP_PROXY=http://127.0.0.1:17891 HTTPS_PROXY=http://127.0.0.1:17891 \
  docker build -t next-edge-studio:1.0.0 .
```
> ⚠️ 本网络阻断 deb.debian.org 的**明文 HTTP**，Dockerfile 里已把源改成 https。
> ⚠️ Docker daemon 的代理是 `system` 模式，但会话内 shell 的 `HTTP_PROXY` 会覆盖它，
> 构建时必须显式传 17891。

### 本地起
```bash
cp docker-compose.yml docker-compose.local.yml   # 镜像名换成本地 tag
docker compose -p studio-test -f docker-compose.local.yml up -d
# http://127.0.0.1:18790
```

### 校验 SoloHost 包
```bash
cd ../listing-assets
python validate_solohost.py ../solohost-studio              # 官方 validator
SOLOHOST_LOCAL_IMAGE=next-edge-studio:1.0.0 \
  bash verify_solohost.sh ../solohost-studio 18795            # 端到端实跑
```

---

## API

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/state` | 应用状态、TTS 引擎、当前任务状态 |
| GET | `/api/assets` | 素材列表 |
| POST | `/api/upload` | 上传素材（multipart，含 16MB+ 视频） |
| DELETE | `/api/assets/<name>` | 删除素材 |
| GET | `/api/project` | 读时间线配置 |
| POST | `/api/project` | 存时间线配置（会校验素材存在） |
| POST | `/api/render` | 触发渲染（写 job.json 给 worker） |
| GET | `/api/status` | 渲染进度 + 日志 |
| GET | `/api/output/<name>` | 成片预览/下载（**支持 Range**，浏览器可拖进度条） |

---

## 数据布局

```
/data                      ← SoloHost named volume
├── assets/                上传的素材
└── work/
    ├── project.json        时间线配置
    ├── job.json           渲染任务（web 写 → worker 读）
    ├── status.json        渲染状态（worker 写 → web 读）
    ├── build/             中间产物（seg_*.mp4 / silent.mp4 / vo/）
    └── out/final.mp4      最终成片
```

---

## SoloHost 契约合规

| 契约要求 | 满足方式 |
|---|---|
| 每个 service 有 `image:` | ✅ 两个 service 同镜像 |
| 恰好一个 `pi.ui.primary: "true"` | ✅ 只有 `web` |
| UI 服务恰好一个 loopback 端口 | ✅ `127.0.0.1:18790:8080` |
| 禁止 bind mount | ✅ 素材走 named volume |
| 禁止 privileged / cap_add / devices | ✅ |
| `${VAR}` 全部声明 | ✅ validator 校验通过 |
| 每个表单选项真正生效 | ✅ 实跑验证（piper 生效、edge 生效） |
| origin fallback | ✅ 不设 `X-Frame-Options`，CORS 放开 |

**官方 validator 实测：`ok: true`**

---

## 镜像体积

| 版本 | 体积 |
|---|---|
| 全量 chromium + 全套 CJK 字体 | 1.58 GB |
| headless-shell + 只留 Regular/Bold 字体 | 1.22 GB |
| + Piper 中文模型（63MB） | **1.29 GB** |
| *对照：官方 Hermes Agent 镜像* | *895 MB* |

大头：`onnxruntime` 78M + `numpy` 73M + `piper` 46M（Piper 本体）
+ chromium 290M + 中文字体 60M。

---

## 已知限制

- **Chrome 需要 `--no-sandbox`**（容器内无 user namespace 权限）
- **内存小的机器可能 OOM**（Chromium + FFmpeg 同时跑）
- **渲染速度取决于 CPU**，14 秒成片约 30–60 秒
- **Piper 音质不如 edge-tts**，断句与韵律还有优化空间
- **尚无移动端适配**（UI 做了窄屏适配但未在手机实测）
