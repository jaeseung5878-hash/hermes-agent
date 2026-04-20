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

ENV PORT=8080 \
    LOG_LEVEL=INFO \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    HERMES_HOME=/data \
    HERMES_QUIET=1

EXPOSE 8080

# Hermes gateway uses Slack Socket Mode (outbound WebSocket) — no HTTP
# listener required. The entrypoint seeds config.yaml on first boot,
# installs the Smart Router shim, then launches the gateway.
CMD ["python", "-m", "custom.entry"]
