#!/usr/bin/env bash
# 构建镜像并（重）启动容器；bootstrap.sh 与 update.sh 共用，避免两处配置漂移。
set -euo pipefail

APP_DIR=/opt/dataagent
REPO_DIR="$APP_DIR/repo"
ENV_FILE="$APP_DIR/.env"
HOST_PORT=8014
CONTAINER_PORT=8000
IMAGE=dataagent-pro:latest
CONTAINER=dataagent-pro

[[ $EUID -eq 0 ]] || { echo "请用 root 运行" >&2; exit 1; }
[[ -f "$ENV_FILE" ]] || { echo "缺少 $ENV_FILE，请先运行 bootstrap.sh" >&2; exit 1; }

docker build -t "$IMAGE" "$REPO_DIR"
docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
docker run -d \
  --name "$CONTAINER" \
  --restart unless-stopped \
  -p "127.0.0.1:$HOST_PORT:$CONTAINER_PORT" \
  --env-file "$ENV_FILE" \
  -v dataagent-data:/app/data \
  "$IMAGE" >/dev/null

for _ in $(seq 1 60); do
  if curl -fsS "http://127.0.0.1:$HOST_PORT/api/health" >/dev/null 2>&1; then
    printf '健康检查通过：http://127.0.0.1:%s/api/health\n' "$HOST_PORT"
    exit 0
  fi
  sleep 2
done

echo "健康检查超时，最近容器日志：" >&2
docker logs --tail 60 "$CONTAINER" >&2 || true
exit 1
