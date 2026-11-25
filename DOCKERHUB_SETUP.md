# Docker Hub 备用通道配置（Next Edge Studio）

> 什么时候需要它：**你的本地网络访问 `ghcr.io` 被 SNI 封锁**（已实测确认）。
> GHCR 是主通道（Pi 节点用户在境外，拉得到）；Docker Hub 是本地可验证的备用通道。

---

## 为什么分成两个 workflow

**踩过的坑**：在 `if:` 表达式里引用不存在的 secret 会让**整个 workflow 解析失败**。

```yaml
# ❌ 这行会让 publish.yml 整个解析失败
if: ${{ secrets.DOCKERHUB_USERNAME != '' }}
```

症状（很有迷惑性）：
- GitHub 把 workflow 的 `name` 回退成**文件路径**（`.github/workflows/publish.yml`）
- `workflow_dispatch` 报 **422 "does not have 'workflow_dispatch' trigger"**
- 看起来像"GitHub 抽风"，其实是 secrets 上下文在**定义阶段**求值失败

实测对照：

| 表达式 | 结果 |
|---|---|
| `if: ${{ github.actor != 'nobody' }}` | ✅ 正常 |
| `if: ${{ secrets.DOCKERHUB_USERNAME != null }}` | ❌ 解析失败 |
| `if: ${{ secrets.DOCKERHUB_USERNAME != '' }}` | ❌ 解析失败 |

**解法**：拆成独立的 `dockerhub.yml`，不用 `if:` 判断 secret 是否存在，
改成**手动触发** —— 要推就触发，没配 secret 时 `docker/login-action` 会明确报错。

---

## 配置步骤（一次性）

### 1. 注册 Docker Hub

<https://hub.docker.com/signup>

### 2. 创建 Access Token

<https://hub.docker.com/settings/security/tokens> → **New Access Token** → 勾 **Read & Write**

> token 是 `dckr_pat_...` 格式（不是 GitHub 的 `ghp_`）

### 3. 在 GitHub 仓库添加 3 个 Secret

仓库：`huiyaozhonghua/next-edge-studio`
路径：**Settings → Secrets and variables → Actions → New repository secret**

| Secret 名称 | 值 |
|---|---|
| `DOCKERHUB_USERNAME` | 你的 Docker Hub 用户名（如 `huiyaozhonghua`） |
| `DOCKERHUB_TOKEN` | 上一步的 `dckr_pat_...` |
| `DOCKERHUB_IMAGE` | `<用户名>/next-edge-studio`（**含仓库名**） |

### 4. 触发推送

```bash
cd G:/WORKBUDDY/2026-11-20-09-48-20/pi-next-app/solohost-studio
bash publish_studio.sh --hub
```

或者在 GitHub 网页上：
<https://github.com/huiyaozhonghua/next-edge-studio/actions/workflows/dockerhub.yml> → **Run workflow**

### 5. 把 Docker Hub 仓库设为 Public（⚠️ 不能漏）

<https://hub.docker.com/r/huiyaozhonghua/next-edge-studio>
→ 右侧 **Settings** → **General** → 最下面 **Repository visibility** → **Public** → **Update visibility**

> 仓库默认是 Private。忘了这步，Pi 节点用户 `docker compose pull` 会 401 失败。

workflow 里的 `Verify anonymous pull` 步骤会**自动验证**这一点 ——
它先 `docker logout docker.io` 再 pull，能拉通就说明是 Public。

### 6. 改 compose 的镜像地址

```yaml
services:
  web:
    image: huiyaozhonghua/next-edge-studio:1.0.0   # Docker Hub
    # 或
    image: ghcr.io/huiyaozhonghua/next-edge-studio:1.0.0   # GHCR（海外用户走这个）
```

---

## 两个通道的取舍

| | GHCR（主） | Docker Hub（备用） |
|---|---|---|
| 海外用户（Pi 节点用户） | ✅ | ✅ |
| 你的本地网络 | ❌ SNI 封锁 | ✅ |
| 官方示例用哪个 | ✅ OpenClaw 用 GHCR | ✅ Grafana 用 Docker Hub |
| 需要额外账号 | 已有 GitHub | 需注册 Docker Hub |

**建议**：提交时用 **GHCR**（Pi 生态评审更熟），本地验证安装流程时用 **Docker Hub**。

---

## 排查

| 现象 | 原因 | 解法 |
|---|---|---|
| workflow 名字显示成文件路径 | `if:` 里引用了不存在的 secret | 删掉该 `if:`，改手动触发 |
| `docker/login-action` 报 401 | token 权限不足或用户名错 | 重新生成 token（Read & Write） |
| push 成功但用户拉不到 | 仓库还是 Private | 改 Repository visibility → Public |
| `image operating system "linux" does not match` | 架构不匹配 | `docker buildx build --platform linux/amd64,linux/arm64` |
| push 卡在 exporting layers | 正常，1.28GB 要几分钟 | 耐心等 |
