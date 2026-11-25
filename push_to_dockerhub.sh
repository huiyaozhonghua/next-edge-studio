#!/usr/bin/env bash
# Docker Hub 推送助手（GHCR 被 SNI 封锁时的替代方案）
#
# 用法:
#   bash push_to_dockerhub.sh <dockerhub用户名> <镜像名> [tag]
# 例:
#   bash push_to_dockerhub.sh huiyaozhonghua next-edge-studio 1.0.0
#
# token 从 https://hub.docker.com/settings/security/tokens 获取（Read & Write）
# 本脚本不接收 token 作为参数 —— 交互式读取，避免进 shell history

set -uo pipefail

DH_USER="${1:?用法: bash push_to_dockerhub.sh <dockerhub用户名> <镜像名> [tag]}"
IMG_NAME="${2:?缺少镜像名}"
TAG="${3:-1.0.0}"

FULL="${DH_USER}/${IMG_NAME}"
LOCAL="${IMG_NAME}:${TAG}"

echo "═══════════════════════════════════════════"
echo " Docker Hub 推送助手"
echo "   本地镜像 : $LOCAL"
echo "   目标     : docker.io/$FULL"
echo "═══════════════════════════════════════════"
echo

# ---------- 大写转小写（Docker Hub 命名空间与仓库名必须全小写）----------
LOWER_USER=$(echo "$DH_USER" | tr '[:upper:]' '[:lower:]')
LOWER_IMG=$(echo "$IMG_NAME" | tr '[:upper:]' '[:lower:]')
if [ "$LOWER_USER" != "$DH_USER" ] || [ "$LOWER_IMG" != "$IMG_NAME" ]; then
  echo "⚠️  已转小写（Docker Hub 要求全小写）:"
  [ "$LOWER_USER" != "$DH_USER" ] && echo "    $DH_USER → $LOWER_USER"
  [ "$LOWER_IMG" != "$IMG_NAME" ] && echo "    $IMG_NAME → $LOWER_IMG"
  DH_USER="$LOWER_USER"; IMG_NAME="$LOWER_IMG"
  FULL="${DH_USER}/${IMG_NAME}"; LOCAL="${IMG_NAME}:${TAG}"
fi

# ---------- 前置检查 ----------
if ! docker image inspect "$LOCAL" >/dev/null 2>&1; then
  echo "❌ 本地没有镜像 $LOCAL"
  echo "   可用镜像："
  docker images --format "   {{.Repository}}:{{.Tag}}  {{.Size}}" | head -10
  exit 1
fi
echo "✅ 本地镜像存在：$LOCAL ($(docker image inspect "$LOCAL" --format '{{.Size}}' | awk '{printf "%.2f GB", $1/1024/1024/1024}'))"

if ! docker info >/dev/null 2>&1; then
  echo "❌ Docker daemon 未运行，先启动 Docker Desktop"
  exit 1
fi

# ---------- 登录 ----------
echo
echo "→ 登录 Docker Hub（粘贴 Access Token，只显示一次）"
echo "  token 在 https://hub.docker.com/settings/security/tokens 获取（Read & Write）"
echo "  粘贴时终端不显示字符，属正常。"
if ! docker login -u "$DH_USER" --password-stdin; then
  echo
  echo "❌ 登录失败。常见原因："
  echo "   · token 少了 Read & Write 权限"
  echo "   · 用户名填错（要和 token 所属账号一致）"
  echo "   · 网络问题 —— 需要代理：export HTTPS_PROXY=http://127.0.0.1:17891"
  exit 1
fi
echo "✅ 登录成功"
echo

# ---------- tag + push ----------
echo "→ 打标签 $FULL"
docker tag "$LOCAL" "$FULL" || exit 1
echo "✅ 已打标签"
echo
echo "→ 推送（1.28GB，首次推需要几分钟）"
echo "  若卡在 'exporting layers' 属正常，别中断。"
echo
if docker push "$FULL"; then
  echo
  echo "✅ 推送成功"
else
  echo
  echo "❌ 推送失败。常见原因："
  echo "   · 401 → token 权限不足或已过期"
  echo "   · 网络 → export HTTPS_PROXY=http://127.0.0.1:17891 再试"
  echo "   · 空间不足 → docker system prune"
  exit 1
fi

# ---------- 验证公开可拉 ----------
echo
echo "→ 验证：匿名拉取（模拟 Pi 节点用户）"
docker logout >/dev/null 2>&1
if docker pull "$FULL" >/dev/null 2>&1; then
  echo "✅ 匿名可拉 —— 仓库已是 Public"
else
  echo "⚠️  匿名拉取失败，仓库多半还是 Private"
  echo "   → 打开 https://hub.docker.com/r/$FULL"
  echo "   → 右侧 Settings → General → 最下面 Repository visibility"
  echo "   → 改成 Public → Update visibility"
  echo "   → 然后重跑本脚本的第 6 步验证"
fi

cat <<EOF

═══════════════════════════════════════════
 下一步
═══════════════════════════════════════════
 1. 把 solohost-studio/docker-compose.yml 里的两处 image 改成：
       image: $FULL

 2. 重新验证包：
       cd ../listing-assets
       bash verify_solohost.sh ../solohost-studio 18795

 3. 去 Pi Desktop 发布（SoloHost → Publish app），
    把两个 yml 内容粘进 Package 步骤，拿 SoloHost App ID。
EOF
