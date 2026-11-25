#!/usr/bin/env bash
# Next Edge Studio —— 一键构建 + 推送到 GHCR / Docker Hub + 校验
#
# 原理：GitHub Actions 的 runner 跑在 GitHub 服务器上，
#       从那里推 ghcr.io / docker.io 都不受你本地网络封锁影响
#       （本网络 ghcr.io 被 SNI 封锁，docker.io 可达但需要 token）
#
# 用法:
#   bash publish_studio.sh              # 只推 GHCR（需要仓库已配好 secrets）
#   bash publish_studio.sh --hub        # 同时推 Docker Hub（需先配 DOCKERHUB_* secrets）
#
# 前置（一次性配置）：
#   GitHub 仓库 → Settings → Secrets and variables → Actions
#   → New repository secret:
#       DOCKERHUB_USERNAME  = 你的 Docker Hub 用户名（仅 --hub 需要）
#       DOCKERHUB_TOKEN    = Docker Hub Access Token (Read & Write)（仅 --hub 需要）
#   → Settings → Secrets and variables → Actions → Variables:
#       DOCKERHUB_IMAGE    = 你的DockerHub用户名/next-edge-studio（仅 --hub 需要）

set -uo pipefail

REPO="huiyaozhonghua/next-edge-studio"
PUSH_HUB=0
[ "${1:-}" = "--hub" ] && PUSH_HUB=1

export HTTP_PROXY=http://127.0.0.1:17891
export HTTPS_PROXY=http://127.0.0.1:17891
export http_proxy=http://127.0.0.1:17891
export https_proxy=http://127.0.0.1:17891

MCP_DIR="C:/Users/HUAWEI/AppData/Local/Temp/pi"

echo "═══════════════════════════════════════════"
echo " Next Edge Studio 发布"
echo "   repo: $REPO"
echo "   mode: $([ $PUSH_HUB -eq 1 ] && echo 'GHCR + Docker Hub' || echo '仅 GHCR')"
echo "═══════════════════════════════════════════"
echo

# ---------- 1. 检查 Docker Hub secrets 是否配好 ----------
if [ $PUSH_HUB -eq 1 ]; then
  echo "▸ 检查仓库 secrets"
  SECRETS=$(curl -s -m 25 -H "Authorization: Bearer ${GITHUB_TOKEN:-}" \
    -H "User-Agent: curl" "https://api.github.com/repos/$REPO/actions/secrets" 2>/dev/null)
  if echo "$SECRETS" | grep -q "DOCKERHUB_USERNAME"; then
    echo "  ✅ DOCKERHUB_USERNAME 已配置"
  else
    echo "  ⚠️  DOCKERHUB_USERNAME 未配置 —— 只推 GHCR"
    PUSH_HUB=0
  fi
fi

# ---------- 2. 触发 workflow ----------
echo "▸ 触发 publish workflow"
TRIGGER=$(curl -s -m 30 -X POST \
  -H "Authorization: Bearer ${GITHUB_TOKEN:-}" \
  -H "Accept: application/vnd.github+json" \
  -H "User-Agent: curl" \
  "https://api.github.com/repos/$REPO/actions/workflows/publish.yml/dispatches" \
  -d '{"ref":"main"}' -w "%{http_code}" 2>/dev/null)
if [ "$TRIGGER" = "204" ]; then
  echo "  ✅ 已触发"
else
  echo "  ⚠️  触发返回 HTTP $TRIGGER（可能缺 GITHUB_TOKEN）"
  echo "     请手动打开：https://github.com/$REPO/actions/workflows/publish.yml"
  echo "     点 Run workflow"
  exit 1
fi

# ---------- 3. 等待完成 ----------
echo "▸ 等待构建（约 2-5 分钟）"
for i in $(seq 1 25); do
  sleep 20
  ST=$(curl -s -m 25 -H "Authorization: Bearer ${GITHUB_TOKEN:-}" \
    -H "Accept: application/vnd.github+json" -H "User-Agent: curl" \
    "https://api.github.com/repos/$REPO/actions/runs" 2>/dev/null | python -c "
import sys,json
try:
    d=json.load(sys.stdin)
    rs=[r for r in d.get('workflow_runs',[]) if r['name'].startswith('Build')]
    print(f\"{rs[0]['status']}/{rs[0].get('conclusion')}\" if rs else 'none')
except Exception: print('none')
" 2>/dev/null)
  echo "   [$i] $ST"
  case "$ST" in completed*) break;; esac
done

echo
echo "═══════════════════════════════════════════"
echo " 完成"
echo "═══════════════════════════════════════════"
echo
echo " 镜像地址："
echo "   GHCR       : ghcr.io/$REPO:latest"
if [ $PUSH_HUB -eq 1 ]; then
  echo "   Docker Hub : docker.io/<你的用户名>/next-edge-studio:latest"
fi
echo
echo " 下一步（提交参赛）："
echo "   1. 去 https://dash.cloudflare.com → Workers & Pages → 找到 Studio 已发布实例"
echo "      （如需重新发布，用 Pi Desktop → Discover SoloHost Apps → Publish app）"
echo "   2. 把 docker-compose.yml 的 image 改成上面那个地址"
echo "   3. 把 config_options.yml 的内容填进 Package 步骤"
echo "   4. 记下 SoloHost App ID"
