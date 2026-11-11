# DataAgent Pro —— 公网演示镜像
# 构建：docker build -t dataagent-pro .
# 运行：docker run -p 8000:8000 -e DATAAGENT_API_KEY=sk-xxx -e DATAAGENT_ACCESS_CODE=xxx dataagent-pro
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATAAGENT_PORT=8000

WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static
COPY sample_data/demo_sales.csv ./sample_data/demo_sales.csv
COPY run.py ./

RUN mkdir -p data/uploads data/exports data/cache

EXPOSE 8000

# 平台会注入 PORT；本地直接 docker run 时回落到 8000。
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips='*'"]
