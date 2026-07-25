# syntax=docker/dockerfile:1.25@sha256:0adf442eae370b6087e08edc7c50b552d80ddf261576f4ebd6421006b2461f12
# =============================================================================
# SynthOrg Fine-Tune -- 2-stage build: builder -> apko-composed Wolfi runtime
# =============================================================================
# Stage 1 (builder): install torch + sentence-transformers + synthorg
#   fine-tune modules into a venv.
# Stage 2 (runtime): apko-composed Wolfi base with BLAS/math libs.
#
# Two variants controlled by FINE_TUNE_EXTRA build-arg:
#   - fine-tune-gpu   (GPU, default): torch with bundled CUDA runtime, ~4 GB download / ~7 GB on disk
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

COPY --from=ghcr.io/astral-sh/uv:0.11.32@sha256:df4cae8f3a96d175e2e5f992e597550000edbe78fdc2594d5cd8de1a217f504c /uv /uvx /bin/

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

# Same contract as docker/backend/Dockerfile: strip whatever bytecode the
# build context carried, then compile our own. This image is spawned as a
# one-shot container per pipeline stage and sets PYTHONDONTWRITEBYTECODE=1,
# so without this every stage launch recompiles all of /app/src and throws
# the result away. See the backend Dockerfile for the measured cost and for
# why -f and the un-swallowed strip are both required.
RUN find /app/src -type d -name __pycache__ -exec rm -rf {} + \
    && python -m compileall -q -f -j 0 --invalidation-mode unchecked-hash /app/src
RUN find /app/.venv -type d -name 'wheels' -exec rm -rf {} + 2>/dev/null || true

# Point venv Python symlink at Wolfi's path (/usr/bin/python).
RUN rm -f /app/.venv/bin/python \
    && ln -s /usr/bin/python /app/.venv/bin/python

# ---------------------------------------------------------------------------
# Stage 2 -- Runtime (apko-composed Wolfi base)
# ---------------------------------------------------------------------------
FROM ${BASE_IMAGE}

ARG FINE_TUNE_EXTRA
# ``image.licenses`` is an SPDX expression covering the project's own
# licence AND the licences of the heavy ML components this image bundles
# (torch + sentence-transformers + transformers + numpy and their
# permissive transitive set), so an image-licence scanner sees the full
# redistribution picture rather than just BUSL-1.1. The
# ``io.synthorg.bundled-licenses`` label enumerates the principal bundled
# components and their licences for human attribution.
LABEL org.opencontainers.image.title="synthorg-${FINE_TUNE_EXTRA}" \
      org.opencontainers.image.description="SynthOrg embedding fine-tuning pipeline (${FINE_TUNE_EXTRA})" \
      org.opencontainers.image.url="https://github.com/Aureliolo/synthorg" \
      org.opencontainers.image.source="https://github.com/Aureliolo/synthorg" \
      org.opencontainers.image.licenses="BUSL-1.1 AND BSD-3-Clause AND Apache-2.0 AND MIT AND PSF-2.0" \
      org.opencontainers.image.vendor="Aureliolo" \
      io.synthorg.bundled-licenses="torch=BSD-3-Clause; sentence-transformers=Apache-2.0; transformers=Apache-2.0; numpy=BSD-3-Clause; tokenizers=Apache-2.0" \
      io.synthorg.fine-tune.variant="${FINE_TUNE_EXTRA}"

WORKDIR /app

COPY --from=builder --chown=10003:10003 /app/.venv /app/.venv
COPY --from=builder --chown=10003:10003 /app/src /app/src

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER 10003

# One-shot batch container spawned per torch-bound stage by the backend
# via the Docker API. No ports, no HEALTHCHECK: the launcher supervises
# via exit code + structured stdout markers.
# Stage config arrives as inline JSON in SYNTHORG_FINE_TUNE_STAGE_CONFIG;
# the shared data volume is mounted read-write at /data (training data
# in, checkpoints out under /data/fine-tune). SYNTHORG_FINE_TUNE_PROBE=1
# runs the dependency/GPU readiness probe instead of a stage.

ENTRYPOINT ["/app/.venv/bin/python", "-m", "synthorg.memory.embedding.fine_tune_runner"]
