# ── 빌드 스테이지 ─────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── 런타임 스테이지 ───────────────────────────────────────────────────────────
FROM python:3.11-slim

# Playwright Chromium 의존성
RUN apt-get update && apt-get install -y --no-install-recommends \
        libnss3 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
        libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 \
        libxfixes3 libxrandr2 libgbm1 libasound2 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 설치된 패키지 복사
COPY --from=builder /install /usr/local

# Playwright Chromium 브라우저 설치
RUN playwright install chromium

# 앱 소스 복사
COPY . .

# Railway가 주입하는 PORT 환경변수 사용 (기본 8080)
ENV PORT=8080 \
    LOG_LEVEL=INFO \
    PYTHONUNBUFFERED=1

EXPOSE 8080

CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
