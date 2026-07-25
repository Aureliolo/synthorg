# syntax=docker/dockerfile:1.25@sha256:0adf442eae370b6087e08edc7c50b552d80ddf261576f4ebd6421006b2461f12
# =============================================================================
# SynthOrg Backend -- 2-stage build: builder -> apko-composed Wolfi runtime
# =============================================================================
# Stage 1 (builder): compile Python deps + fix venv symlink for Wolfi paths.
# Stage 2 (runtime): apko-composed distroless Wolfi base (no shell, no pkg
#   manager). CIS Docker Benchmark v1.6.0 compliant (hardening via compose.yml).
#
# The base image is composed declaratively by docker/backend/apko.yaml and
# published by CI. This Dockerfile layers the uv-built venv and app source on
# top. No setup stage needed -- the symlink fix is done in the builder itself.
# =============================================================================

# CI always provides BASE_IMAGE explicitly and uses a digest-pinned ref when
# available (otherwise a tag/local artifact tag). No default: standalone
# builds without an explicit --build-arg BASE_IMAGE=... fail fast instead of
# silently pulling a mutable :latest tag (Scorecard Pinned-Dependencies).
ARG BASE_IMAGE

# ---------------------------------------------------------------------------
# Stage 1 -- Builder (ephemeral: compiles deps, fixes venv symlink)
# ---------------------------------------------------------------------------
FROM python:3.14.3-slim@sha256:5e59aae31ff0e87511226be8e2b94d78c58f05216efda3b07dbbed938ec8583b AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.32@sha256:df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN addgroup --gid 65532 --system build \
    && adduser --uid 65532 --system --ingroup build --home /home/build build \
    && mkdir /app && chown build:build /app

WORKDIR /app

COPY --chown=65532:65532 pyproject.toml uv.lock ./
USER build
# Install the `postgres`, `distributed`, and `telemetry` extras
# unconditionally. `postgres` and `distributed` are runtime-selectable
# via `synthorg init --persistence-backend` and `--bus-backend`, so the
# image must carry psycopg and nats-py whether or not the user has
# chosen them yet. `telemetry` ships the logfire SDK so the opt-in
# project telemetry signal actually reaches Logfire when operators flip
# `SYNTHORG_TELEMETRY_ENABLED=true` -- shipping the image without it would make
# the opt-in a silent noop. `fine-tune` lives in the dedicated
# synthorg-fine-tune-gpu / -cpu images (large, torch inside).
RUN --mount=type=cache,target=/home/build/.cache/uv,uid=65532,gid=65532 \
    uv sync --frozen --no-dev --no-install-project --extra postgres --extra distributed --extra telemetry

RUN touch README.md
COPY --chown=65532:65532 src/ src/
COPY --chown=65532:65532 scripts/embed_logfire_token.py scripts/embed_logfire_token.py

# Embed the write-only Logfire project token into source. The
# secret is supplied at build time via ``--build-arg
# LOGFIRE_PROJECT_TOKEN=...`` (CI sets it from the GHA repo secret;
# operators building locally leave it unset). When unset the
# script is skipped and ``_embedded_token.py`` keeps its sentinel
# value, so the resulting image has telemetry disabled-by-build.
# The embed runs *before* ``uv sync`` packages the venv so the
# rewritten module is what gets cached in the wheel cache and
# copied into the runtime stage.
ARG LOGFIRE_PROJECT_TOKEN=""
RUN if [ -n "$LOGFIRE_PROJECT_TOKEN" ]; then \
        python scripts/embed_logfire_token.py "$LOGFIRE_PROJECT_TOKEN"; \
    else \
        echo "LOGFIRE_PROJECT_TOKEN unset; shipping sentinel (telemetry disabled-by-build)"; \
    fi

RUN --mount=type=cache,target=/home/build/.cache/uv,uid=65532,gid=65532 \
    uv sync --frozen --no-dev --extra postgres --extra distributed --extra telemetry

# Ship the application's bytecode, and only the build's own.
#
# Importing a .py compiles it to bytecode first; CPython normally caches
# that in __pycache__ so later imports skip the compile. This runtime
# cannot: PYTHONDONTWRITEBYTECODE=1 is set below and compose mounts the
# container read_only. So a container shipped without cached bytecode
# recompiles all of /app/src on every start and discards the result.
# Measured A/B on identical source in this image at --cpus 2, importing
# synthorg.api.app: 31.6s without cached bytecode, 20.4s with, i.e. 11.2s
# (35%) of every boot. The venv's third-party bytecode already ships via
# UV_COMPILE_BYTECODE=1; this brings the application in line with it.
#
# The strip runs FIRST and is load-bearing, not redundant with
# .dockerignore: any __pycache__ that reached the build context carries
# the *developer's* bytecode, including typeguard-instrumented variants
# from a local test run. Discarding it and recompiling from source is what
# makes the shipped bytecode a product of the build rather than of
# whichever machine ran it.
#
# unchecked-hash pins cache validity to source content rather than
# mtime+size, so the COPY into the runtime stage cannot produce a stale
# hit, and it embeds no timestamps (reproducible builds). compileall exits
# non-zero on a syntax error, which is a build-time signal worth failing on.
RUN find /app/src -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true \
    && python -m compileall -q -j 0 --invalidation-mode unchecked-hash /app/src

# Strip cached wheels only. Keep *.dist-info so Syft and other SBOM tooling
# can detect actual installed Python packages (not just declared dependencies).
RUN find /app/.venv -type d -name 'wheels' -exec rm -rf {} + 2>/dev/null || true

# Point venv Python symlink at Wolfi's path (/usr/bin/python) instead of
# the builder's (/usr/local/bin/python3.14).  Creates a dangling symlink
# in the builder that resolves when COPY'd to the Wolfi runtime stage.
RUN rm -f /app/.venv/bin/python \
    && ln -s /usr/bin/python /app/.venv/bin/python

# Pre-create the data directory tree in the builder so COPY --chown can
# transfer it to the shell-less runtime stage with correct ownership.
# Switch back to root for creating /data, then re-chown to the build user.
USER root
RUN mkdir -p /data/logs /data/memory && chown -R 65532:65532 /data
USER build

# ---------------------------------------------------------------------------
# Stage 2 -- Runtime (apko-composed Wolfi base: no shell, no package manager)
# ---------------------------------------------------------------------------
FROM ${BASE_IMAGE}

# Baked deployment-environment tag. Release-tag builds pass
# `prod`; `-dev.N` / `-rc.*` / `-alpha.*` / `-beta.*` pre-release
# tag builds pass `pre-release`; everything else (main pushes, PR
# builds, local `docker build`) keeps the `dev` default. Runtime
# can still override via SYNTHORG_TELEMETRY_ENV; CI auto-detection
# also wins over this baked value. See
# synthorg.telemetry.collector._resolve_environment for the full
# priority chain.
#
# The write-only telemetry-backend token is embedded into source at
# build time by the builder stage's ``embed_logfire_token.py`` step
# (gated on the optional ``LOGFIRE_PROJECT_TOKEN`` build-arg). When
# the build-arg is unset, the sentinel stays in place and the image
# is telemetry-disabled-by-build. Operators do NOT supply a runtime
# env var for the token; the only operator-facing knob is
# ``SYNTHORG_TELEMETRY_ENABLED`` which gates the opt-in itself.
ARG DEPLOYMENT_ENV="dev"

LABEL org.opencontainers.image.title="synthorg-backend" \
      org.opencontainers.image.description="SynthOrg orchestration backend" \
      org.opencontainers.image.url="https://github.com/Aureliolo/synthorg" \
      org.opencontainers.image.source="https://github.com/Aureliolo/synthorg" \
      org.opencontainers.image.licenses="BUSL-1.1 AND LGPL-3.0-only" \
      org.opencontainers.image.vendor="Aureliolo"

WORKDIR /app

COPY --from=builder --chown=65532:65532 /app/.venv /app/.venv
COPY --from=builder --chown=65532:65532 /app/src /app/src
COPY --chown=65532:65532 pyproject.toml uv.lock /app/

# Pre-create the data directory with correct ownership so Docker
# initialises fresh volumes with uid/gid 65532 instead of root.
# COPY from builder stage because the distroless runtime has no shell.
COPY --from=builder --chown=65532:65532 /data /data

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    SYNTHORG_HOST="0.0.0.0" \
    SYNTHORG_PORT="3001" \
    UVICORN_HOST="0.0.0.0" \
    UVICORN_PORT="3001" \
    SYNTHORG_TELEMETRY_ENV_BAKED=${DEPLOYMENT_ENV} \
    PYTHONWARNINGS="ignore::UserWarning:litestar.plugins.pydantic.utils"

USER 65532
EXPOSE 3001

# Liveness, not readiness: a container HEALTHCHECK gates process restart,
# so it must probe /healthz (always 200 while the event loop is
# responsive). Probing /readyz would restart the container whenever a
# dependency is transiently unready -- readiness gates traffic, not restart.
#
# The budget is derived from measurement, not guessed. uvicorn runs the
# whole ASGI lifespan (persistence connect, migrations, service wiring)
# BEFORE it binds the listening socket, so every probe until boot completes
# fails with a connection refused -- the start period has to cover the
# entire cold boot, not just the interpreter start.
#
# Measured on a fresh install (new volumes, so every migration applies),
# container start -> first passing probe:
#   43.7s  full stack, warm image cache, this image's precompiled bytecode
#   238s   cold image cache, no precompiled bytecode, several services
#          contending for the same 2 CPUs -- the worst case observed in
#          the field, and what the budget has to absorb
#
# start-period = 600s is ~2.5x that worst case. It costs nothing on a
# healthy boot: the first passing probe ends the start period immediately,
# which is what start-interval is for. It only bounds how long a genuinely
# hung boot is tolerated before compose gives up. A crashed process is
# detected by compose straight away and never waits this out.
#
# start-interval (Docker 25+) probes every 2s *during* the start period, so
# a fast boot is detected within ~2s instead of inheriting the 10s
# steady-state interval. An older daemon ignores the field and falls back
# to --interval, so this stays compatible with the 20.10 floor the CLI
# enforces.
#
# timeout = 10s against a measured probe cost of 0.5-0.9s under boot
# contention: the probe spawns a fresh interpreter, so it competes for CPU
# with the boot it is probing. interval and retries are unchanged -- they
# govern steady-state liveness, which was never the failure.
HEALTHCHECK --interval=10s --timeout=10s --retries=3 --start-period=600s --start-interval=2s \
    CMD ["/app/.venv/bin/python", "-c", "import json,urllib.request,sys,os; port=os.environ.get('UVICORN_PORT','3001'); r=urllib.request.urlopen(f'http://127.0.0.1:{port}/api/v1/healthz'); d=json.loads(r.read()); sys.exit(0 if d.get('data',{}).get('status')=='ok' else 1)"]

ENTRYPOINT []
CMD ["/app/.venv/bin/uvicorn", "synthorg.api.app:create_app", "--factory", "--no-access-log"]
