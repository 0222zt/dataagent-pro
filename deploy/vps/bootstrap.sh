#!/usr/bin/env bash
# DataAgent Pro · Oracle Cloud Always Free VPS 一键部署（幂等，可重复运行 = 更新）
#
# 先把脚本下载到本机再执行，这样才能交互输入密钥：
#   curl -fsSL -o /tmp/da-setup.sh https://raw.githubusercontent.com/0222zt/dataagent-pro/main/deploy/vps/bootstrap.sh
#   sudo bash /tmp/da-setup.sh
# 免交互（注意命令会留在 shell history 里）：
#   sudo DA_API_KEY=sk-xxx DA_ACCESS_CODE=xxxxxxxx bash /tmp/da-setup.sh
#
# 适用：Ubuntu 22.04 / 24.04（ARM64 或 x86_64），Oracle Cloud VM.Standard.A1.Flex。
set -euo pipefail

APP_DIR=/opt/dataagent
REPO_DIR="$APP_DIR/repo"
REPO_URL=https://github.com/0222zt/dataagent-pro.git
HOST_PORT=8014
ENV_FILE="$APP_DIR/.env"
CF_LOG="$APP_DIR/cloudflared.log"
SCRIPTS="$REPO_DIR/deploy/vps"

log() { printf '\n\033[1;32m==>\033[0m %s\n' "$*"; }
die() { printf '\n\033[1;31m错误：\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "请用 root 运行：sudo bash bootstrap.sh"
case "$(uname -m)" in
  aarch64|arm64) CF_ASSET=cloudflared-linux-arm64 ;;
  x86_64|amd64)  CF_ASSET=cloudflared-linux-amd64 ;;
  *) die "不支持的架构 $(uname -m)；Oracle A1 应为 aarch64" ;;
esac
command -v systemctl >/dev/null 2>&1 || die "需要 systemd（请使用 Ubuntu 镜像，不要用容器镜像）"

log "1/7 安装 Docker / git / curl"
if ! command -v docker >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq docker.io curl ca-certificates git openssl >/dev/null
fi
systemctl enable --now docker >/dev/null
docker --version

log "2/7 安装 cloudflared ($CF_ASSET)"
curl -fsSL "https://github.com/cloudflare/cloudflared/releases/latest/download/$CF_ASSET" \
  -o /usr/local/bin/cloudflared
chmod 0755 /usr/local/bin/cloudflared
cloudflared --version

log "3/7 拉取 / 更新代码"
mkdir -p "$APP_DIR"
if [[ -d "$REPO_DIR" && ! -d "$REPO_DIR/.git" ]]; then
  die "$REPO_DIR 已存在且不是 git 仓库，请手动处理"
fi
if [[ -d "$REPO_DIR/.git" ]]; then
  git -C "$REPO_DIR" fetch --depth 1 origin main
  git -C "$REPO_DIR" reset --hard origin/main
else
  git clone --depth 1 "$REPO_URL" "$REPO_DIR"
fi

log "4/7 配置环境变量"
if [[ -f "$ENV_FILE" ]]; then
  log "保留已有 $ENV_FILE（要换密钥请先删除该文件再重跑）"
else
  API_KEY="${DA_API_KEY:-}"
  ACCESS_CODE="${DA_ACCESS_CODE:-}"
  if [[ -z "$API_KEY" ]]; then read -r -s -p "SiliconFlow API Key: " API_KEY; printf '\n'; fi
  if [[ -z "$ACCESS_CODE" ]]; then read -r -s -p "访问码（8 位以上，访客打开链接时输入）: " ACCESS_CODE; printf '\n'; fi
  [[ -n "$API_KEY" ]] || die "API Key 不能为空"
  [[ ${#ACCESS_CODE} -ge 8 ]] || die "访问码至少 8 位"
  umask 077
  cat > "$ENV_FILE" <<EOF
DATAAGENT_API_KEY=$API_KEY
DATAAGENT_ACCESS_CODE=$ACCESS_CODE
DATAAGENT_DEMO=1
DATAAGENT_PROVIDER=siliconflow
DATAAGENT_MODEL=deepseek-ai/DeepSeek-V4.1-Flash
DATAAGENT_FALLBACK_MODELS=Qwen/Qwen3-8B,Qwen/Qwen3.5-4B,deepseek-ai/DeepSeek-R1-0528-Qwen3-8B,THUDM/GLM-4-9B-0414
DATAAGENT_MAX_FILE_SIZE=5242880
DATAAGENT_MAX_ROWS=20000
DATAAGENT_MAX_DATASETS=5
DATAAGENT_DATASET_TTL=3600
DATAAGENT_RATE_LIMIT=30
DATAAGENT_LLM_RATE_LIMIT=3
DATAAGENT_LLM_DAILY_LIMIT=50
DATAAGENT_EXEC_TIMEOUT=10
DATAAGENT_LOG_LEVEL=INFO
EOF
  chmod 600 "$ENV_FILE"
  umask 022
fi

log "5/7 构建镜像并启动容器"
bash "$SCRIPTS/start-container.sh"

log "6/7 注册 systemd 服务"
cat > /etc/systemd/system/dataagent-tunnel.service <<'UNIT'
[Unit]
Description=DataAgent Pro Cloudflare Quick Tunnel
After=network-online.target docker.service
Wants=network-online.target
Requires=docker.service

[Service]
Type=simple
ExecStart=/usr/local/bin/cloudflared tunnel --url http://127.0.0.1:8014 --no-autoupdate --loglevel info
Restart=always
RestartSec=5
StandardOutput=append:/opt/dataagent/cloudflared.log
StandardError=append:/opt/dataagent/cloudflared.log

[Install]
WantedBy=multi-user.target
UNIT

cat > /etc/systemd/system/dataagent-publish.service <<'UNIT'
[Unit]
Description=Publish current DataAgent Pro quick tunnel URL

[Service]
Type=oneshot
ExecStart=/bin/bash /opt/dataagent/repo/deploy/vps/publish-url.sh
UNIT

cat > /etc/systemd/system/dataagent-publish.timer <<'UNIT'
[Unit]
Description=Refresh DataAgent Pro public URL every 30s

[Timer]
OnBootSec=20s
OnUnitActiveSec=30s
Persistent=true

[Install]
WantedBy=timers.target
UNIT

cat > /etc/systemd/system/dataagent-keepalive.service <<'UNIT'
[Unit]
Description=DataAgent Pro light keepalive (Oracle Always Free idle policy)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
EnvironmentFile=-/opt/dataagent/keepalive.env
ExecStart=/usr/bin/python3 /opt/dataagent/repo/deploy/vps/keepalive.py
Restart=always
RestartSec=30
Nice=19

[Install]
WantedBy=multi-user.target
UNIT

chmod 0755 "$SCRIPTS"/*.sh
touch "$CF_LOG"
systemctl daemon-reload
systemctl enable --now dataagent-tunnel.service dataagent-publish.timer dataagent-keepalive.service
systemctl restart dataagent-tunnel.service

log "7/7 等待公网链接"
URL=""
for _ in $(seq 1 45); do
  bash "$SCRIPTS/publish-url.sh" >/dev/null 2>&1 || true
  if [[ -s "$APP_DIR/public_url.txt" ]]; then URL="$(tr -d '\r\n' < "$APP_DIR/public_url.txt")"; fi
  if [[ -n "$URL" ]]; then break; fi
  sleep 2
done
[[ -n "$URL" ]] || die "90 秒内没拿到隧道链接，排查：journalctl -u dataagent-tunnel -n 60 --no-pager"

printf '\n\033[1;32m部署完成\033[0m\n'
printf '  公网链接：%s\n' "$URL"
printf '  访问码  ：%s\n' "$(grep '^DATAAGENT_ACCESS_CODE=' "$ENV_FILE" | cut -d= -f2-)"
printf '  链接文件：%s/public_url.txt（域名变化时自动更新）\n' "$APP_DIR"
printf '\n常用命令：\n'
printf '  查隧道日志  journalctl -u dataagent-tunnel -f\n'
printf '  查应用日志  docker logs -f dataagent-pro\n'
printf '  更新到最新  sudo bash %s/update.sh\n' "$SCRIPTS"
printf '  看当前链接  cat %s/public_url.txt\n' "$APP_DIR"
