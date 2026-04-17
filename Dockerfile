# ── 빌드 스테이지 ─────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── 런타임 스테이지 ───────────────────────────────────────────────────────────
FROM python:3.11-slim

WORKDIR /app

# 설치된 패키지 복사
COPY --from=builder /install /usr/local

# Playwright가 자체적으로 필요한 시스템 패키지를 설치
# (playwright install-deps가 Debian 버전에 맞게 자동 처리)
RUN apt-get update && apt-get install -y --no-install-recommends \
        wget curl \
    && playwright install-deps chromium \
    && playwright install chromium \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# 앱 소스 복사
COPY . .

ENV PORT=8080 \
    LOG_LEVEL=INFO \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

EXPOSE 8080

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
