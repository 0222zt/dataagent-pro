#!/usr/bin/env bash
# 把 VPS 上的服务更新到 GitHub main 最新版（.env 里的密钥保持不变）。
set -euo pipefail

APP_DIR=/opt/dataagent
REPO_DIR="$APP_DIR/repo"
SCRIPTS="$REPO_DIR/deploy/vps"

[[ $EUID -eq 0 ]] || { echo "请用 root 运行：sudo bash update.sh" >&2; exit 1; }

git -C "$REPO_DIR" fetch --depth 1 origin main
git -C "$REPO_DIR" reset --hard origin/main
bash "$SCRIPTS/start-container.sh"

# 隧道进程重建后 trycloudflare.com 域名会变，重启并让 publish 抓新链接。
OLD_URL="$(cat "$APP_DIR/public_url.txt" 2>/dev/null || true)"
systemctl restart dataagent-tunnel.service
for _ in $(seq 1 30); do
  bash "$SCRIPTS/publish-url.sh" >/dev/null 2>&1 || true
  NEW_URL="$(cat "$APP_DIR/public_url.txt" 2>/dev/null || true)"
  if [[ -n "$NEW_URL" && "$NEW_URL" != "$OLD_URL" ]]; then break; fi
  sleep 2
done
printf '当前公网链接：%s\n' "$(cat "$APP_DIR/public_url.txt" 2>/dev/null || echo '尚未生成，稍后查看 journalctl -u dataagent-tunnel')"
