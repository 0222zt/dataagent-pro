#!/usr/bin/env bash
# 从 cloudflared 日志里取最新的 trycloudflare.com 链接；域名变化时才写文件并可选推送通知。
# systemd timer 每 30 秒跑一次，所以隧道意外重连后链接会自动刷新。
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/dataagent}"
LOG="$APP_DIR/cloudflared.log"
URL_FILE="$APP_DIR/public_url.txt"
HISTORY="$APP_DIR/url-history.log"

[[ -s "$LOG" ]] || exit 0
URL="$(grep -oE 'https://[A-Za-z0-9-]+\.trycloudflare\.com' "$LOG" | tail -n 1 || true)"
[[ -n "$URL" ]] || exit 0

if [[ -f "$URL_FILE" ]]; then
  if [[ "$(tr -d '\r\n' < "$URL_FILE")" == "$URL" ]]; then
    exit 0
  fi
fi

printf '%s\n' "$URL" > "$URL_FILE"
printf '%s  %s\n' "$(date -Is)" "$URL" >> "$HISTORY"

# 可选：把新链接推到任意接受 {"text": "..."} 的 webhook（飞书 / 企业微信机器人等）。
# 只需在 /opt/dataagent/notify.env 写一行 NOTIFY_WEBHOOK=https://...
if [[ -f "$APP_DIR/notify.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "$APP_DIR/notify.env"
  set +a
  if [[ -n "${NOTIFY_WEBHOOK:-}" ]]; then
    curl -fsS -m 10 -X POST -H 'Content-Type: application/json' \
      -d "{\"text\":\"DataAgent Pro 新链接：$URL\"}" "$NOTIFY_WEBHOOK" >/dev/null 2>&1 || true
  fi
fi

printf '公网链接已更新：%s\n' "$URL"
