# syntax=docker/dockerfile:1.24@sha256:87999aa3d42bdc6bea60565083ee17e86d1f3339802f543c0d03998580f9cb89
# =============================================================================
# SynthOrg Fine-Tune -- 2-stage build: builder -> apko-composed Wolfi runtime
# =============================================================================
# Stage 1 (builder): install torch + sentence-transformers + synthorg
#   fine-tune modules into a venv.
# Stage 2 (runtime): apko-composed Wolfi base with BLAS/math libs.
#
# Two variants controlled by FINE_TUNE_EXTRA build-arg:
#   - fine-tune-gpu   (GPU, default): torch with bundled CUDA runtime, ~4 GB
#   - fine-tune-cpu   (CPU-only, pinned via [tool.uv.sources]):     ~1.7 GB
#
# Only rebuilt when docker/fine-tune/** or
# src/synthorg/memory/embedding/fine_tune*.py changes, or on release tags.
# =============================================================================

ARG BASE_IMAGE
ARG FINE_TUNE_EXTRA=fine-tune-gpu

# ---------------------------------------------------------------------------
# Stage 1 -- Builder (installs ML deps + synthorg fine-tune code)
# ---------------------------------------------------------------------------
FROM python:3.14.3-slim@sha256:5e59aae31ff0e87511226be8e2b94d78c58f05216efda3b07dbbed938ec8583b AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.14@sha256:1025398289b62de8269e70c45b91ffa37c373f38118d7da036fb8bb8efc85d97 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN addgroup --gid 10003 --system fine-tune \
    && adduser --uid 10003 --system --ingroup fine-tune --home /home/fine-tune fine-tune \
    && mkdir /app && chown fine-tune:fine-tune /app

WORKDIR /app

COPY --chown=10003:10003 pyproject.toml uv.lock ./
USER fine-tune
# FINE_TUNE_EXTRA is either "fine-tune-gpu" (bundled CUDA torch) or
# "fine-tune-cpu" (CPU-only torch routed via [tool.uv.sources]).
ARG FINE_TUNE_EXTRA
RUN --mount=type=cache,target=/home/fine-tune/.cache/uv,uid=10003,gid=10003 \
    uv sync --frozen --no-dev --no-install-project --group "${FINE_TUNE_EXTRA}"

RUN touch README.md
COPY --chown=10003:10003 src/ src/
RUN --mount=type=cache,target=/home/fine-tune/.cache/uv,uid=10003,gid=10003 \
    uv sync --frozen --no-dev --group "${FINE_TUNE_EXTRA}"

RUN find /app/src -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
RUN find /app/.venv -type d -name 'wheels' -exec rm -rf {} + 2>/dev/null || true

# Point venv Python symlink at Wolfi's path (/usr/bin/python).
RUN rm -f /app/.venv/bin/python \
    && ln -s /usr/bin/python /app/.venv/bin/python

# ---------------------------------------------------------------------------
# Stage 2 -- Runtime (apko-composed Wolfi base)
# ---------------------------------------------------------------------------
FROM ${BASE_IMAGE}

ARG FINE_TUNE_EXTRA
LABEL org.opencontainers.image.title="synthorg-${FINE_TUNE_EXTRA}" \
      org.opencontainers.image.description="SynthOrg embedding fine-tuning pipeline (${FINE_TUNE_EXTRA})" \
      org.opencontainers.image.url="https://github.com/Aureliolo/synthorg" \
      org.opencontainers.image.source="https://github.com/Aureliolo/synthorg" \
      org.opencontainers.image.licenses="BUSL-1.1" \
      org.opencontainers.image.vendor="Aureliolo" \
      io.synthorg.fine-tune.variant="${FINE_TUNE_EXTRA}"

WORKDIR /app

COPY --from=builder --chown=10003:10003 /app/.venv /app/.venv
COPY --from=builder --chown=10003:10003 /app/src /app/src

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER 10003

# No network exposure -- fine-tuning is a batch job.
# Config is mounted at /etc/fine-tune/config.json by the orchestrator.
# Source data is mounted at /data (read-only).
# Checkpoints written to /checkpoints (read-write).

# The runner starts an HTTP health server on the port resolved by
# ``synthorg.memory.embedding.fine_tune_runner._resolve_health_port``
# (env ``SYNTHORG_FINE_TUNE_HEALTH_PORT``, default ``15002``). The
# probe reads the same env var so an operator override stays in sync
# with the actual listener.
HEALTHCHECK --interval=10s --timeout=3s --retries=3 --start-period=30s \
    CMD ["/app/.venv/bin/python", "-c", "import os,urllib.request,sys\np=int(os.environ.get('SYNTHORG_FINE_TUNE_HEALTH_PORT','15002'))\nif not 1<=p<=65535: raise ValueError('SYNTHORG_FINE_TUNE_HEALTH_PORT out of range [1, 65535]')\nr=urllib.request.urlopen(f'http://127.0.0.1:{p}/healthz',timeout=2)\nsys.exit(0 if r.status==200 else 1)"]

ENTRYPOINT ["/app/.venv/bin/python", "-m", "synthorg.memory.embedding.fine_tune_runner"]
