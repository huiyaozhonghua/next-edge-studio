# GHCR 配置指南（Next Edge Studio）

> 目标：把 `next-edge-studio:1.0.0` 推到 `ghcr.io`，让 Pi 节点用户能匿名拉取并安装。
> 全程约 10 分钟。

---

## 你现在的环境（已探测）

| 项 | 状态 |
|---|---|
| Docker Desktop | ✅ 已装 4.99.0，daemon 运行中（29.9.3） |
| Docker 凭据后端 | ✅ `credsStore: desktop` —— 走 Windows 凭据管理器，**不写进 config.json** |
| 本地镜像 | ✅ `next-edge-studio:1.0.0`（1.28GB）已构建 |
| GitHub Desktop | ✅ 已装 |
| git-credential-manager | ✅ 2.9.0 |
| `gh` CLI | ❌ 未装（**不需要**，见下） |
| `~/.docker/config.json` 的 `auths` | `{}` —— **这是正常的**，见下 |

> ### 💡 为什么 `auths` 是空的？
> 你的 Docker 配了 `"credsStore": "desktop"`，凭据由 **Docker Desktop 的系统钥匙串**托管，
> 根本不落在 `config.json` 里。所以「auths 是空的」≠「没登录过」，
> 也**不需要手工编辑这个文件**。登录后凭据会在 Windows 凭据管理器里，`config.json` 依然可能显示 `{}`。

---

## 第 1 步：创建 Personal Access Token

GitHub 官方要求用 PAT，**不能用 GitHub 密码**。

### 操作路径

1. 打开 <https://github.com/settings/tokens>
   （或：右上角头像 → Settings → 左侧最下面 **Developer settings** → **Personal access tokens** → **Tokens (classic)**）

2. 点 **Generate new token (classic)**

3. 填写：
   | 字段 | 填什么 |
   |---|---|
   | Note | `next-edge-studio-ghcr`（随便写，方便以后认出来） |
   | Expiration | 建议 **90 days**（到期重新生成；classic PAT 不设过期就是永久，但永久 token 风险大） |
   | Scopes | 勾选 **`write:packages`** 和 **`read:packages`** |

   > `write:packages` = 推送镜像（**必需**）
   > `read:packages` = 拉取（建议勾）
   > `delete:packages` = 删版本（可选，用不上）
   > **不要勾 `repo` / `delete_repo` 之类**，越少越好

4. 点 **Generate token**

5. **立刻复制那个 token**（`ghp_...` 开头）
   —— GitHub **只显示这一次**，关掉页面就再也看不到了。

> ⚠️ **token 不要贴在对话里、不要发给我、不要提交进 git。**
> 它等同于你的 GitHub 账号密码。

---

## 第 2 步：确认你的 GitHub 用户名

GHCR 的镜像路径是 `ghcr.io/<你的用户名>/<镜像名>`，**用户名和镜像名必须全小写**。

查用户名：打开 <https://github.com/settings/profile>，最上面 **Public profile** 里的
**username** 就是（不是你显示用的昵称）。

> 脚本会自动把大写转小写，但你自己心里要有个数 —— 因为后面 GitHub 网页上找包也要用。

---

## 第 3 步：登录 + 推送

我写了个脚本，把容易错的地方都固化了（用户名大小写、镜像名大小写、
PAT 交互式输入不进 history、失败原因诊断、推完验证匿名可拉取）。

```bash
cd G:/WORKBUDDY/2026-11-20-09-48-20/pi-next-app/solohost-studio
bash push_to_ghcr.sh <你的GitHub用户名> next-edge-studio 1.0.0
```

脚本会：
1. 检查本地镜像存在 ✅
2. 自动转小写（如需要）并提示
3. **交互式**提示你粘贴 PAT（终端不回显字符，粘完回车）
4. `docker login ghcr.io -u <用户名> --password-stdin`
5. `docker tag` + `docker push`
6. **验证匿名可访问**（模拟 Pi 节点用户拉取）
7. 打印后续要改的 compose image 行

### 手动版（等价）

如果你想自己敲：

```bash
# 1) 登录（PAT 只在这条命令的输入流里，不进 history）
echo "ghp_你的TOKEN" | docker login ghcr.io -u 你的用户名 --password-stdin

# 2) 打标签
docker tag next-edge-studio:1.0.0 ghcr.io/你的用户名/next-edge-studio:1.0.0

# 3) 推送（1.28GB，几分钟）
docker push ghcr.io/你的用户名/next-edge-studio:1.0.0
```

> ⚠️ **`docker login -p TOKEN` 这种写法别用** —— token 会进 shell history 和进程列表。
> 必须用 `--password-stdin`。

---

## 第 4 步：把包设为 Public（**这步不能漏**）

GHCR 的包**默认是 private**。如果忘了这步：

- Pi 节点用户 `docker compose pull` 会 **401 失败**
- 评审/其他人都装不了你的 App
- **比赛直接废掉**

### 操作路径

1. 打开 <https://github.com/users/你的用户名/packages>
2. 点进 **next-edge-studio**
3. 右侧栏 **Package settings**
4. 滚到最下面 **Danger Zone** → **Change visibility**
5. 选 **Public**
6. 输入包名 `next-edge-studio` 确认
7. 点 **I understand the consequences, change package visibility**

### 验证匿名可拉取

```bash
# 不带任何凭据拉一下（Docker 会用本机已登录的凭据，所以更严格的验证是：
# 在浏览器里直接访问下面这个 URL，能打开 JSON 就说明是公开的）
https://ghcr.io/v2/你的用户名/next-edge-studio/manifests/1.0.0
```

或者用 `curl`（走代理）：

```bash
curl -s -o /dev/null -w "%{http_code}\n" \
  -H "Accept: application/vnd.oci.image.index.v1+json" \
  -x http://127.0.0.1:17891 \
  "https://ghcr.io/v2/你的用户名/next-edge-studio/manifests/1.0.0"
# 200 = 公开可访问 ✅
# 401 = 还是 private ❌
```

---

## 第 5 步：改 compose 并重新验证

把 `solohost-studio/docker-compose.yml` 里的两处 image 改成你的：

```yaml
image: ghcr.io/你的用户名/next-edge-studio:1.0.0
```

然后重新验证包（这次会真的从 GHCR 拉）：

```bash
cd ../listing-assets
bash verify_solohost.sh ../solohost-studio 18795
```

---

## 第 6 步：去 Pi Desktop 发布

1. 打开 Pi Desktop → 左侧 **Discover SoloHost Apps** → 右上 **Publish app**
2. 4 步表单：
   - **App Details** —— 英文名 **Next Edge Studio**，英文描述（见 `README.md`）
   - **Package** —— 把 `docker-compose.yml` 和 `config_options.yml` 的内容分别粘进两个文本框
   - **Preview** —— 检查渲染的安装表单
   - **Review & publish** —— 可见性选 **Unlisted — link only**
3. 发布后进详情页点 **Install** → 填表单 → **Save and install**
4. **记下 SoloHost App ID**（形如 `你的用户名/next-edge-studio`）—— 提交黑客松时要填

---

## 故障排查速查

| 症状 | 原因 | 解法 |
|---|---|---|
| `401 Unauthorized`（push 时） | PAT 过期 / 缺 `write:packages` / 用户名不匹配 | 重新生成 PAT；确认 `-u` 填的是 PAT 所有者 |
| `403 Forbidden`（push 时） | 认证成功但无权限 | 检查 token scopes；确认是个人账号不是 org |
| `denied` / `No such image` | 包不存在或还没推 | 先 `docker push` |
| `unauthorized`（用户 pull 时） | **包还是 private** | 回第 4 步改 Public ← **最常见** |
| `image operating system "linux" does not match` | 架构不匹配（你在 arm64 Mac 上构建） | `docker buildx build --platform linux/amd64,linux/arm64` |
| push 卡在 `exporting layers` | 正常，1.28GB 要几分钟 | 耐心等，别中断 |
| push 报网络错误 | 需要代理 | `export HTTPS_PROXY=http://127.0.0.1:17891` 再推 |

---

## 安全提醒

- PAT 等同账号密码。**别贴进对话、别写进文件、别提交 git**
- 镜像里**不要**烘进任何密钥（API Key、私钥、助记词）
  - 本项目检查过：`Dockerfile` 里只有公开依赖 + Piper 语音模型，**无任何凭证** ✅
- 推完可以**撤销 PAT**（不影响已推送的镜像），但那样就没法再推新版本了
  —— 建议保留到比赛结束（2027-01-06）之后再撤

---

## 你也可以不用 GHCR

如果嫌 GitHub 配置麻烦，**Docker Hub 也行**（<https://hub.docker.com>）：

```bash
docker login -u 你的账号        # 同样是 PAT/密码
docker tag next-edge-studio:1.0.0 你的账号/next-edge-studio:1.0.0
docker push 你的账号/next-edge-studio:1.0.0
```

差别：Docker Hub 默认也是私有，要手动设 Public；免费账户有**镜像存储配额限制**
（个人版 1 个私有仓库 + 无限公开？具体看当前政策，1.28GB 的公开镜像一般没问题）。

**官方 SoloHost 示例用的就是 `ghcr.io/openclaw/openclaw`** —— 评审看 GHCR 更熟悉。
