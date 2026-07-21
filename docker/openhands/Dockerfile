# syntax=docker/dockerfile:1.25@sha256:0adf442eae370b6087e08edc7c50b552d80ddf261576f4ebd6421006b2461f12
# =============================================================================
# SynthOrg OpenHands sandbox -- sandbox base + bundled openhands-sdk
# =============================================================================
# Extends the apko-composed sandbox base (docker/sandbox, python-3.14 +
# nodejs + git) and layers the OpenHands V1 software-agent-sdk plus its
# agent_server into a DEDICATED, isolated virtualenv at /opt/openhands. The
# SDK lives ONLY in this image, never the main package venv, so the
# litellm / pyo3-3.14 pin holds that constrain the app are irrelevant here:
# this venv resolves openhands-sdk's own dependency closure independently.
#
# The adapter (src/synthorg/engine/openhands/) drives the agent_server
# started below over REST/WS. The agent reaches models only through the LLM
# gateway and credentialed tools only through the credentialed-MCP endpoint;
# all other egress stays blocked by the sidecar (docker/sidecar/).
# =============================================================================

ARG BASE_IMAGE
FROM ${BASE_IMAGE}

LABEL org.opencontainers.image.title="synthorg-openhands" \
      org.opencontainers.image.description="SynthOrg OpenHands inner-loop sandbox" \
      org.opencontainers.image.url="https://github.com/Aureliolo/synthorg" \
      org.opencontainers.image.source="https://github.com/Aureliolo/synthorg" \
      org.opencontainers.image.licenses="BUSL-1.1" \
      org.opencontainers.image.vendor="Aureliolo"

# Pin the SDK; bump deliberately and re-run the live smoke on every change.
ARG OPENHANDS_SDK_VERSION=1.36.1
ARG AGENT_SERVER_PORT=15004

ENV OPENHANDS_VENV=/opt/openhands/venv \
    OPENHANDS_AGENT_SERVER_PORT=${AGENT_SERVER_PORT}

USER 0

# Isolated venv so the SDK closure never mingles with the app runtime.
RUN python3 -m venv "${OPENHANDS_VENV}" \
    && "${OPENHANDS_VENV}/bin/pip" install --no-cache-dir --upgrade pip \
    && "${OPENHANDS_VENV}/bin/pip" install --no-cache-dir \
        "openhands-sdk==${OPENHANDS_SDK_VERSION}" \
        "openhands-agent-server==${OPENHANDS_SDK_VERSION}" \
    && chown -R 10001:10001 /opt/openhands

WORKDIR /workspace
USER 10001

EXPOSE ${AGENT_SERVER_PORT}

HEALTHCHECK --interval=10s --timeout=3s --retries=3 --start-period=10s \
    CMD wget -qO- "http://127.0.0.1:${OPENHANDS_AGENT_SERVER_PORT}/health" || exit 1

# The agent_server binds loopback+container network; the sidecar is the only
# egress path and the adapter attaches over the container-internal address.
CMD ["sh", "-c", "\"${OPENHANDS_VENV}/bin/python\" -m openhands.agent_server --host 0.0.0.0 --port \"${OPENHANDS_AGENT_SERVER_PORT}\""]
