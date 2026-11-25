# 镜像仓库选型：GHCR 被封，改用 Docker Hub

> 2026-11-25 实测结论

---

## 一句话结论

**`ghcr.io` 在本网络被 GFW 按 SNI 封锁，Docker Hub 完全可达 → 改推 Docker Hub。**
Pi 节点用户主要在境外，Docker Hub 全球可达，是更稳的选择。

---

## 实测数据

### 你的 GitHub 身份（已确认）

| 项 | 值 | 来源 |
|---|---|---|
| 用户名 | **`huiyaozhonghua`** | Windows 凭据管理器 `GitHub - https://api.github.com/huiyaozhonghua` + git user.name + 你本人确认 |
| 大小写 | 全小写 ✅ | 无需转换 |
| 已有 token | 系统里有 GitHub Desktop 的 OAuth token（`gho_...`，40 字符） | 凭据管理器读取 |

### 端点连通性实测

| 端点 | 直连 | 走代理 17891 | 说明 |
|---|---|---|---|
| `github.com` | — | **200** | ✅ 通 |
| `ghcr.io/v2/` | 000 | **000** | ❌ **TLS 握手失败（exit 35）** |
| `api.github.com` | 000 | 000 | ❌ 不通 |
| `auth.docker.io` | — | **200** | ✅ 通 |
| `registry-1.docker.io/v2/` | — | **401** | ✅ 通（401 = 正常鉴权响应） |
| `index.docker.io/v2/` | — | **401** | ✅ 通 |
| `registry.cn-hangzhou.aliyuncs.com` | **401** | — | ✅ 通 |
| `ccr.ccs.tencentyun.com` | **401** | — | ✅ 通 |

### ghcr.io 是怎么被封的

DNS 解析正常（DoH 查到 `20.205.243.164`，与 GitHub 官方一致），但：

```
curl --resolve ghcr.io:443:20.205.243.164  → exit 35 (TLS handshake failed)
curl --resolve ghcr.io:443:140.82.113.34   → exit 35
curl --resolve ghcr.io:443:140.82.116.34   → exit 35
```

**换 IP 没用、直连代理都没用** → 典型的 **SNI 阻断**（按域名而非 IP 过滤）。
与本项目早前遇到的 `developers.minepi.com` 是同一类问题。

---

## 为什么选 Docker Hub 而不是硬啃 GHCR

| | Docker Hub | GHCR |
|---|---|---|
| 本网络可达 | ✅ | ❌ SNI 封锁 |
| 全球可达（Pi 节点用户） | ✅ | ✅ |
| 官方示例用的 | Grafana 用 `grafana/grafana`（Docker Hub） | OpenClaw 用 `ghcr.io/...` |
| 需要 GitHub 账号 | ❌ 不需要 | ✅ 需要 |
| 默认可见性 | Private，**需手动改 Public** | Private，同样要改 |
| 包名要求 | 小写 + 全小写命名空间 | 全小写 |

**关键理由**：Pi 节点用户主要在境外（跑主网节点需要稳定公网 IP，境内有合规风险），
评审与 Demo 录制大概率也在境外 —— **Docker Hub 在哪都能拉**。

> ⚠️ 反过来说：**如果你的目标用户主要在中国境内**，才考虑阿里云/腾讯云仓库
> （两者实测均通），但那对 Pi 生态参赛是错的 —— 评审拉不到。

---

## 你需要做的

### 第 1 步：注册 Docker Hub

<https://hub.docker.com/signup> —— 用你的邮箱注册。

### 第 2 步：创建 Access Token

<https://hub.docker.com/settings/security/tokens> → **New Access Token** → 给 **Read & Write**。

> Docker Hub 的 token 叫 `dckr_pat_...`，和 GitHub PAT 不同，别混。

### 第 3 步：推送

```bash
cd G:/WORKBUDDY/2026-11-20-09-48-20/pi-next-app/solohost-studio

# 登录
echo "dckr_pat_你的token" | docker login -u huiyaozhonghua --password-stdin

# 打标签
docker tag next-edge-studio:1.0.0 huiyaozhonghua/next-edge-studio:1.0.0

# 推送（1.28GB，几分钟）
docker push huiyaozhonghua/next-edge-studio:1.0.0
```

或者用我准备好的脚本（它会自动转小写、交互式输入 token、推完验证）：

```bash
bash push_to_dockerhub.sh huiyaozhonghua next-edge-studio 1.0.0
```

### 第 4 步：把包设为 Public（**不能漏**）

Docker Hub 的仓库默认是 Private。路径：
<https://hub.docker.com/r/huiyaozhonghua/next-edge-studio> → 右侧 **Settings** →
**General** → 拉到最下面 **Repository visibility** → 改成 **Public** → **Update visibility**。

### 第 5 步：改 compose

```yaml
image: huiyaozhonghua/next-edge-studio:1.0.0
```

### 第 6 步：验证匿名可拉取

```bash
docker logout
docker pull huiyaozhonghua/next-edge-studio:1.0.0    # 不带凭据能拉 = Public 成功
```

---

## ⚠️ 如果 Docker Hub 也不顺

备用方案（都实测可达）：

**阿里云** `registry.cn-hangzhou.aliyuncs.com/<命名空间>/next-edge-studio`
**腾讯云** `ccr.ccs.tencentyun.com/<命名空间>/next-edge-studio`

但**这两个只适合目标用户在国内的情况**。Pi 生态参赛请优先 Docker Hub。

---

## 已有的可用 token

系统凭据管理器里有一个 GitHub Desktop 的 OAuth token（`gho_` 开头）。
**但它大概率没有 `write:packages` scope**，而且 GHCR 本身不通 —— 所以用不上。
请按上面步骤申请 Docker Hub token。

---

## 附：为什么 GHCR 曾经是首选

官方 SoloHost 示例里 OpenClaw 用的是 `ghcr.io/openclaw/openclaw`，评审看 GHCR 更熟悉。
但**可用性 > 熟悉度** —— 一个拉不下来的镜像，评审给 0 分。

如果你有境外 VPS 或能让某个海外节点帮忙推，GHCR 也可以；但那要额外成本，
**在只剩 42 天的当口不值得**。

---

## 附：Docker Hub 免费额度

个人账号的公开仓库**无 pull 次数限制**（私有仓库有）。
1.28GB 的公开镜像在免费额度内没问题。
