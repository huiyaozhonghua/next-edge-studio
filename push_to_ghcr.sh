#!/usr/bin/env bash
# GHCR 推送助手 —— 把容易踩坑的地方固化成脚本
#
# 用法:
#   bash push_to_ghcr.sh <github用户名> <镜像名> [tag]
# 例:
#   bash push_to_ghcr.sh zjh18981927828 next-edge-studio 1.0.0
#
# 前置: 你已经拿到 PAT（Personal Access Token classic，勾选 write:packages）
# 本脚本不接收 PAT 作为参数 —— 交互式读取，避免进 shell history

set -uo pipefail

GH_USER="${1:?用法: bash push_to_ghcr.sh <github用户名> <镜像名> [tag]}"
IMG_NAME="${2:?缺少镜像名}"
TAG="${3:-1.0.0}"

FULL="ghcr.io/${GH_USER}/${IMG_NAME}"
LOCAL="${IMG_NAME}:${TAG}"

echo "═══════════════════════════════════════════"
echo " GHCR 推送助手"
echo "   本地镜像 : $LOCAL"
echo "   目标     : $FULL"
echo "═══════════════════════════════════════════"
echo

# ---------- 1) 用户名必须小写 ----------
if [ "$(echo "$GH_USER" | tr '[:upper:]' '[:lower:]')" != "$GH_USER" ]; then
  echo "⚠️  GitHub 用户名含大写字母，GHCR 要求全小写，已自动转换："
  GH_USER_LOWER=$(echo "$GH_USER" | tr '[:upper:]' '[:lower:]')
  echo "    $GH_USER → $GH_USER_LOWER"
  FULL="ghcr.io/${GH_USER_LOWER}/${IMG_NAME}"
  GH_USER="$GH_USER_LOWER"
fi

# ---------- 2) 镜像名也必须小写 ----------
if [ "$(echo "$IMG_NAME" | tr '[:upper:]' '[:lower:]')" != "$IMG_NAME" ]; then
  IMG_NAME_LOWER=$(echo "$IMG_NAME" | tr '[:upper:]' '[:lower:]')
  echo "⚠️  镜像名含大写字母，已转小写：$IMG_NAME → $IMG_NAME_LOWER"
  IMG_NAME="$IMG_NAME_LOWER"
  LOCAL="${IMG_NAME}:${TAG}"
  FULL="ghcr.io/${GH_USER}/${IMG_NAME}"
fi

# ---------- 3) 检查本地镜像 ----------
if ! docker image inspect "$LOCAL" >/dev/null 2>&1; then
  echo "❌ 本地没有镜像 $LOCAL"
  echo "   可用镜像："
  docker images --format "   {{.Repository}}:{{.Tag}}  {{.Size}}" | head -10
  exit 1
fi
echo "✅ 本地镜像存在：$LOCAL ($(docker image inspect "$LOCAL" --format '{{.Size}}' | awk '{printf "%.2f GB", $1/1024/1024/1024}'))"

# ---------- 4) 检查 docker daemon 是否活着 ----------
if ! docker info >/dev/null 2>&1; then
  echo "❌ Docker daemon 未运行，先启动 Docker Desktop"
  exit 1
fi

# ---------- 5) 登录 ----------
echo
echo "→ 登录 ghcr.io（输入 PAT，只显示一次，粘贴后回车）"
echo "  PAT 需勾选 write:packages；粘贴时终端不会显示字符，属正常。"
if ! docker login ghcr.io -u "$GH_USER" --password-stdin; then
  echo
  echo "❌ 登录失败。常见原因："
  echo "   · PAT 少了 write:packages 权限（少了也能登录，但 push 会 403）"
  echo "   · 用户名填错（要和 PAT 的所有者一致）"
  echo "   · PAT 已过期或已撤销"
  exit 1
fi
echo "✅ 登录成功"
echo

# ---------- 6) tag ----------
echo "→ 打标签 $FULL"
docker tag "$LOCAL" "$FULL" || exit 1
echo "✅ 已打标签"
echo

# ---------- 7) push ----------
echo "→ 推送（1.28GB，首次推需要几分钟）"
echo "  提示：若卡在 'finalizing' 或 ' exporting layers'，属正常，别中断。"
echo
if docker push "$FULL"; then
  echo
  echo "✅ 推送成功"
else
  echo
  echo "❌ 推送失败。常见原因："
  echo "   · 401 → PAT 过期/无 write:packages/用户名不匹配"
  echo "   · 403 → 权限不足，或包可见性设置阻止"
  echo "   · 网络 → 需要代理：export HTTPS_PROXY=http://127.0.0.1:17891"
  exit 1
fi

# ---------- 8) 验证可拉取 ----------
echo
echo "→ 验证：匿名拉取（模拟 Pi 节点用户）"
if docker manifest inspect "$FULL" >/dev/null 2>&1; then
  echo "✅ 镜像清单可访问"
else
  echo "⚠️  清单不可访问（包还是 private）"
  echo "   → 打开 https://github.com/users/$GH_USER/packages"
  echo "   → 点进 next-edge-studio → 右侧 Package settings"
  echo "   → 最下面 Danger Zone → Change visibility → Public"
  echo "   → 输入包名确认"
fi

cat <<EOF

═══════════════════════════════════════════
 下一步
═══════════════════════════════════════════
 1. 把 soloHost-studio/docker-compose.yml 里的 image 改成：
       image: $FULL

 2. 重新验证包：
       cd ../listing-assets
       bash verify_solohost.sh ../solohost-studio 18795

 3. 去 Pi Desktop 发布（SoloHost → Publish app），
    把两个 yml 内容粘进 Package 步骤，拿 SoloHost App ID。
EOF
