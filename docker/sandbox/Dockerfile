# syntax=docker/dockerfile:1.26@sha256:ecfaec9ed6d810b56388c508f4121597bfbba70d41a6dfeee4d8cad5f295fc32
# =============================================================================
# SynthOrg Sandbox -- thin wrapper over apko-composed Wolfi base
# =============================================================================
# The base image (ghcr.io/aureliolo/synthorg-sandbox-base) is composed
# declaratively by docker/sandbox/apko.yaml and published by CI.  It includes
# busybox, git, python-3.14, and nodejs-25.  Network enforcement is handled
# by the sidecar proxy container (docker/sidecar/), not by the sandbox.
# =============================================================================

ARG BASE_IMAGE
FROM ${BASE_IMAGE}

LABEL org.opencontainers.image.title="synthorg-sandbox" \
      org.opencontainers.image.description="SynthOrg agent code execution sandbox" \
      org.opencontainers.image.url="https://github.com/Aureliolo/synthorg" \
      org.opencontainers.image.source="https://github.com/Aureliolo/synthorg" \
      org.opencontainers.image.licenses="BUSL-1.1" \
      org.opencontainers.image.vendor="Aureliolo"

WORKDIR /workspace

COPY --chown=10001:10001 docker/sandbox/healthz.py /usr/local/bin/healthz.py

USER 10001

HEALTHCHECK --interval=10s --timeout=3s --retries=3 --start-period=5s \
    CMD wget -qO- http://127.0.0.1:15003/healthz || exit 1

CMD ["sh", "-c", "python3 /usr/local/bin/healthz.py & sleep infinity"]
