# syntax=docker/dockerfile:1

# =============================================================================
# SynthOrg Backend — Multi-stage, CIS-hardened container
# =============================================================================
# Three stages: builder → setup → runtime (distroless).
# Chainguard Python distroless: minimal attack surface (no shell, no package
# manager), non-root by default (UID 65532), continuously rebuilt by Chainguard.
# CIS Docker Benchmark v1.6.0 compliant.
# All base images pinned by version or digest (Dependabot auto-updates).
# =============================================================================

# ---------------------------------------------------------------------------
# Stage 1 — Builder (ephemeral: compiles deps + project wheel)
# ---------------------------------------------------------------------------
FROM python:3.14.3-slim@sha256:6a27522252aef8432841f224d9baaa6e9fce07b07584154fa0b9a96603af7456 AS builder

COPY --from=ghcr.io/astral-sh/uv:0.10.9@sha256:10902f58a1606787602f303954cea099626a4adb02acbac4c69920fe9d278f82 /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app

# Install dependencies first (layer cache — only invalidated on lock change)
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# Copy source and install project.
# touch README.md: hatchling requires README.md for package metadata but
# .dockerignore excludes it — create an empty file to satisfy the build.
RUN touch README.md
COPY src/ src/
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Strip __pycache__ from source (bytecode already compiled by uv into .venv)
RUN find /app/src -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# ---------------------------------------------------------------------------
# Stage 2 — Setup (ephemeral: fix symlinks + create dirs, needs shell)
# ---------------------------------------------------------------------------
# Chainguard Python -dev variant (has shell for RUN commands).
# Pinned by digest — free tier only exposes latest/latest-dev tags.
# To update: docker buildx imagetools inspect cgr.dev/chainguard/python:latest-dev
FROM cgr.dev/chainguard/python@sha256:4decc0a7de3d586bbb38307244f32a10f50c96627714ac2785a347a8b63ceeb4 AS setup

USER root
WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY --from=builder /app/src /app/src

# Fix venv Python symlink: builder points to /usr/local/bin/python3,
# but Chainguard Python is at /usr/bin/python
RUN rm -f /app/.venv/bin/python && ln -s /usr/bin/python /app/.venv/bin/python

# Create data directory for persistent volumes
RUN mkdir -p /data && chown 65532:65532 /data

# Set final ownership
RUN chown -R 65532:65532 /app

# ---------------------------------------------------------------------------
# Stage 3 — Runtime (distroless: no shell, no uv, no package manager)
# ---------------------------------------------------------------------------
# Chainguard Python distroless — minimal attack surface, continuously scanned.
# Pinned by digest — free tier only exposes latest/latest-dev tags.
# To update: docker buildx imagetools inspect cgr.dev/chainguard/python:latest
FROM cgr.dev/chainguard/python@sha256:308d2fda1d9ad9620454de97cb84fa407bd20e24dd66902909f4b3b44b310836 AS runtime

# OCI image labels (https://github.com/opencontainers/image-spec/blob/main/annotations.md)
LABEL org.opencontainers.image.title="synthorg-backend" \
      org.opencontainers.image.description="SynthOrg orchestration backend" \
      org.opencontainers.image.url="https://github.com/Aureliolo/synthorg" \
      org.opencontainers.image.source="https://github.com/Aureliolo/synthorg" \
      org.opencontainers.image.licenses="BUSL-1.1" \
      org.opencontainers.image.vendor="Aureliolo"

WORKDIR /app

# Copy prepared venv, source, and data dir from setup stage
COPY --from=setup --chown=65532:65532 /app/.venv /app/.venv
COPY --from=setup --chown=65532:65532 /app/src /app/src
COPY --from=setup --chown=65532:65532 /data /data

# Runtime environment
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    AI_COMPANY_HOST="0.0.0.0" \
    AI_COMPANY_PORT="8000"

USER 65532

EXPOSE 8000

# Healthcheck — exec form (no shell needed, works with distroless)
HEALTHCHECK --interval=10s --timeout=5s --retries=3 --start-period=15s \
    CMD ["/usr/bin/python", "-c", "import json,urllib.request,sys; r=urllib.request.urlopen('http://localhost:8000/api/v1/health'); d=json.loads(r.read()); sys.exit(0 if d.get('data',{}).get('status')=='healthy' else 1)"]

# Reset Chainguard base entrypoint (/usr/bin/python) so CMD runs uvicorn
# directly instead of being passed as Python arguments.
ENTRYPOINT []
CMD ["/app/.venv/bin/uvicorn", "ai_company.api.app:create_app", "--factory", \
     "--host", "0.0.0.0", "--port", "8000"]
