# syntax=docker/dockerfile:1.24
# =============================================================================
# SynthOrg Desktop -- headless virtual-desktop sandbox image
# =============================================================================
# Provides a bounded GUI runtime the agent drives via the desktop tool:
# Xvfb supplies a headless X server, fluxbox places windows, xdotool injects
# pointer / keyboard input, and scrot captures screenshots. Python 3 with the
# Tk toolkit lets the org's GUI deliverables run without an external display.
#
# Network enforcement is handled by the sidecar proxy container, not here.
# The vision verifier consumes screenshots written under the mounted
# workspace (/workspace/.synthorg/desktop/screenshots).
# =============================================================================

FROM debian:trixie-slim

LABEL org.opencontainers.image.title="synthorg-desktop" \
      org.opencontainers.image.description="SynthOrg agent virtual desktop sandbox" \
      org.opencontainers.image.url="https://github.com/Aureliolo/synthorg" \
      org.opencontainers.image.source="https://github.com/Aureliolo/synthorg" \
      org.opencontainers.image.licenses="BUSL-1.1" \
      org.opencontainers.image.vendor="Aureliolo"

ENV DEBIAN_FRONTEND=noninteractive \
    DISPLAY=:99

# hadolint ignore=DL3008
RUN apt-get update \
    && apt-get install --no-install-recommends -y \
        xvfb \
        x11-utils \
        x11vnc \
        fluxbox \
        xdotool \
        scrot \
        python3 \
        python3-tk \
        bash \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace

# Run as an unprivileged user; the workspace is bind-mounted with matching ownership.
RUN useradd --uid 10001 --create-home --shell /bin/bash synthorg
USER 10001

# Liveness: the image is healthy when the desktop toolchain the executor
# depends on is present on PATH. Xvfb is only started on first tool use, so
# the check verifies the binaries rather than a running display.
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD command -v Xvfb && command -v xdotool && command -v scrot || exit 1

# Keep-alive entrypoint: the sandbox lifecycle drives work via `docker exec`
# (the desktop tool's executor brings Xvfb up idempotently on first use).
CMD ["sleep", "infinity"]
