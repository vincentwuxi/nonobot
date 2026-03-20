# ───────────────────────────────────────────
# NonoBot Docker Build
# Multi-stage: deps → runtime (slim)
# ───────────────────────────────────────────

# Stage 1: Builder — install dependencies
FROM python:3.12-slim AS builder

WORKDIR /build

# System deps for compilation (cffi, lxml, etc.)
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc g++ libffi-dev libxml2-dev libxslt1-dev \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md LICENSE ./
COPY nanobot/ nanobot/
COPY bridge/ bridge/

# Install into a virtual environment for clean copy
RUN python -m venv /opt/venv \
    && /opt/venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/venv/bin/pip install --no-cache-dir .

# ───────────────────────────────────────────
# Stage 2: Runtime — minimal image
# ───────────────────────────────────────────
FROM python:3.12-slim AS runtime

LABEL maintainer="nonobot contributors"
LABEL description="NonoBot — Personal AI Assistant Gateway"

# Runtime deps only
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2 libxslt1.1 curl ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && useradd -m -s /bin/bash nonobot

# Copy venv from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

# Create workspace directories
RUN mkdir -p /home/nonobot/.nanobot/sandbox \
    && chown -R nonobot:nonobot /home/nonobot

WORKDIR /home/nonobot
USER nonobot

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -sf http://localhost:${NONOBOT_PORT:-18790}/health || exit 1

# Default port
EXPOSE ${NONOBOT_PORT:-18790}

# Entry point
CMD ["nanobot", "gateway"]
