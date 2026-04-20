FROM python:3.11-slim

WORKDIR /app

# System packages:
# - curl: healthchecks / debug
# - git:  setuptools_scm / some installers read git metadata
# - build-essential: needed for PyJWT[crypto] / cffi native builds
RUN apt-get update && apt-get install -y --no-install-recommends \
        curl \
        git \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy entire source tree. Hermes package layout pulls from many top-level
# modules (run_agent.py, utils.py, hermes_constants.py, agent/, gateway/, ...),
# so we cannot do a minimal-copy install.
COPY . .

RUN pip install --no-cache-dir -U pip setuptools wheel \
    && pip install --no-cache-dir -e ".[slack,cron]"

# Patchright runtime for Claude A2A (skills/claude_a2a drives claude.ai).
# Patchright is a Playwright fork that removes CDP-layer fingerprints
# (runtime.enable leak, console.* hook, etc.) which Cloudflare Enterprise
# uses to detect stock Playwright even with stealth JS patches. We still
# keep `playwright` installed because scripts/login_a2a.py runs locally
# headed for interactive session capture and does not need CF bypass.
#
# `install-deps` pulls the shared libs Chromium needs (nss, atk, cups,
# xkbcommon, x11, composite, randr, gbm, alsa, pango, ...). Chromium
# itself is fetched by `patchright install chromium`. Kept in a separate
# layer so non-A2A code changes don't invalidate the ~500 MB download cache.
RUN pip install --no-cache-dir "playwright>=1.44.0" "patchright>=1.44.0" \
    && playwright install-deps chromium \
    && patchright install chromium

ENV PORT=8080 \
    LOG_LEVEL=INFO \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HERMES_HOME=/data \
    HERMES_QUIET=1

EXPOSE 8080

# Hermes gateway uses Slack Socket Mode (outbound WebSocket) — no HTTP
# listener required for ingress. The entrypoint seeds config.yaml on first
# boot, installs the Smart Router + Slack patches, starts the local A2A
# OpenAI-compatible proxy (127.0.0.1:8888), then launches the gateway.
CMD ["python", "-m", "custom.entry"]
