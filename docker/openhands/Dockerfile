# syntax=docker/dockerfile:1.26@sha256:ecfaec9ed6d810b56388c508f4121597bfbba70d41a6dfeee4d8cad5f295fc32
# =============================================================================
# SynthOrg OpenHands sandbox -- sandbox base + bundled openhands-sdk
# =============================================================================
# Extends the apko-composed sandbox base (docker/sandbox, python-3.14 +
# nodejs + git + bash + tmux) and layers the OpenHands V1 SDK (openhands-sdk +
# openhands-tools) into a DEDICATED, isolated virtualenv at /opt/openhands.
#
# The SDK lives ONLY in this image, never the main package venv. This is a
# dependency-hygiene isolation: openhands-sdk + openhands-tools pull a
# large hash-pinned closure (litellm 1.93, fastmcp, openai, browser-use, grpcio,
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
#
# Deliberately no HEALTHCHECK, unlike docker/sandbox: this is a one-shot task
# runner with no long-lived process to probe, so a healthcheck would report
# "starting" for the container's entire lifetime.
#
# The egress pin is two-layer. allowed_hosts permits the backend's host:port;
# allowed_paths narrows it to the gateway and credentialed-MCP path prefixes,
# because both endpoints live in the same backend process as its auth,
# metrics and webhook routes, and a host:port allowlist alone grants those too.
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

# The one package the lockfile cannot hash-pin, because it is the installer
# that reads it. Left floating, a rebuild of an identical lockfile can resolve
# a different pip, which is the only unaudited input to this image.
# renovate: datasource=pypi depName=pip
ARG PIP_VERSION=26.2

USER 0

# Isolated venv + hash-pinned closure so the SDK deps never mingle with the
# app runtime and every build resolves the identical, audited dependency set.
COPY docker/openhands/requirements.lock /opt/openhands/requirements.lock
RUN python3 -m venv "${OPENHANDS_VENV}" \
    && "${OPENHANDS_VENV}/bin/pip" install --no-cache-dir --upgrade "pip==${PIP_VERSION}" \
    && "${OPENHANDS_VENV}/bin/pip" install --no-cache-dir --require-hashes \
        -r /opt/openhands/requirements.lock \
    # pip is a build-time tool: the entrypoint never invokes it, and nothing
    # installs anything at runtime (the closure is fixed and hash-pinned).
    # Shipping it means shipping its vendored tree, which carries its own
    # advisories against code this image never executes. Same for the base's
    # bootstrap wheels, already consumed by the venv above.
    && rm -rf "${OPENHANDS_VENV}"/lib/python*/site-packages/pip \
        "${OPENHANDS_VENV}"/lib/python*/site-packages/pip-*.dist-info \
        "${OPENHANDS_VENV}"/bin/pip* \
        /usr/share/python-wheels \
    && chown -R 10001:10001 /opt/openhands

# After the closure: the RUN above depends only on requirements.lock, so this
# ordering keeps the whole pip layer cached whenever only the entrypoint
# changes, which is most of the time.
COPY --chown=10001:10001 docker/openhands/run_task.py /opt/openhands/run_task.py

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
