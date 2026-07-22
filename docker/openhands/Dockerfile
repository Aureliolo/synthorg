# syntax=docker/dockerfile:1.25@sha256:0adf442eae370b6087e08edc7c50b552d80ddf261576f4ebd6421006b2461f12
# =============================================================================
# SynthOrg OpenHands sandbox -- sandbox base + bundled openhands-sdk
# =============================================================================
# Extends the apko-composed sandbox base (docker/sandbox, python-3.14 +
# nodejs + git + bash + tmux) and layers the OpenHands V1 SDK (openhands-sdk +
# openhands-tools) into a DEDICATED, isolated virtualenv at /opt/openhands.
#
# The SDK lives ONLY in this image, never the main package venv. This is a
# dependency-hygiene isolation: openhands-sdk + openhands-tools pull a
# ~338-package closure (litellm 1.93, fastmcp, openai, browser-use, grpcio,
# ...) and a litellm version that conflicts with the app's pinned litellm;
# keeping it here avoids polluting the app's carefully-pinned dependency set.
# (It installs cleanly on 3.14; the isolation is not about a build
# incompatibility.)
#
# The agent runs to completion INSIDE this container against a LocalWorkspace
# via /opt/openhands/run_task.py, so its native terminal/file_editor tools
# execute here, never on the host. It reaches models only through the LLM
# gateway and credentialed tools only through the credentialed-MCP endpoint;
# the container's egress is pinned to those two hosts by the sidecar
# (docker/sidecar/). The host-side adapter
# (engine/openhands/container_runtime.py) spawns this container, feeds the run
# spec on stdin, and parses the normalized JSON event stream from stdout.
# =============================================================================

ARG BASE_IMAGE
FROM ${BASE_IMAGE}

LABEL org.opencontainers.image.title="synthorg-openhands" \
      org.opencontainers.image.description="SynthOrg OpenHands inner-loop sandbox" \
      org.opencontainers.image.url="https://github.com/Aureliolo/synthorg" \
      org.opencontainers.image.source="https://github.com/Aureliolo/synthorg" \
      org.opencontainers.image.licenses="BUSL-1.1" \
      org.opencontainers.image.vendor="Aureliolo"

# The SDK version is pinned solely by requirements.in / requirements.lock (the
# hash-pinned closure installed below); bump it there and re-run the live smoke
# on every change.
ENV OPENHANDS_VENV=/opt/openhands/venv

USER 0

# Isolated venv + hash-pinned closure so the SDK deps never mingle with the
# app runtime and every build resolves the identical, audited dependency set.
COPY docker/openhands/requirements.lock /opt/openhands/requirements.lock
COPY docker/openhands/run_task.py /opt/openhands/run_task.py
RUN python3 -m venv "${OPENHANDS_VENV}" \
    && "${OPENHANDS_VENV}/bin/pip" install --no-cache-dir --upgrade pip \
    && "${OPENHANDS_VENV}/bin/pip" install --no-cache-dir --require-hashes \
        -r /opt/openhands/requirements.lock \
    && chown -R 10001:10001 /opt/openhands

WORKDIR /workspace
USER 10001

ENV OPENHANDS_SUPPRESS_BANNER=1

# The container's egress is pinned to the gateway + credentialed-MCP hosts, so
# litellm's default attempt to fetch its remote model-cost map is a doomed
# call the sidecar blocks on every run; force the bundled local map instead.
ENV LITELLM_LOCAL_MODEL_COST_MAP=True

# One task per container: the host adapter feeds the run spec on stdin and
# reads normalized JSON events from stdout, then tears the container down. A
# CMD (not ENTRYPOINT) so the host can pass the same command explicitly
# through the hardened container config without arg-prepending an entrypoint;
# `docker run -i <image>` still runs it standalone.
CMD ["/opt/openhands/venv/bin/python", "/opt/openhands/run_task.py"]
