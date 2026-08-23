# syntax=docker/dockerfile:1.26@sha256:ecfaec9ed6d810b56388c508f4121597bfbba70d41a6dfeee4d8cad5f295fc32
# =============================================================================
# SynthOrg Sandbox -- thin wrapper over apko-composed Wolfi base
# =============================================================================
# The base image (ghcr.io/aureliolo/synthorg-sandbox-base) is composed
# declaratively by docker/sandbox/apko.yaml and published by CI.  It includes
# busybox, git, python-3.14, and nodejs-24.  Network enforcement is handled
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

# Probed with the same python3 that serves it two lines below, which is the one
# HTTP client this image is guaranteed to have: the Wolfi busybox below carries
# no wget applet and apko.yaml adds no curl, so a shell one-liner calling wget
# reports `wget: not found` on every probe and leaves every sandbox container
# permanently unhealthy (FailingStreak 30 in a measured run). Probing through
# the server's own interpreter cannot drift either, because if python3 were
# missing there would be nothing listening to probe.
#
# -I is load-bearing, not tidiness. `python3 -c` puts the CWD at the head of
# sys.path, WORKDIR is the agent-writable /workspace, so without it an agent
# can drop a urllib.py there and have this run it every ten seconds. Isolated
# mode drops that entry and PYTHONPATH with it. A shell probe never had the
# exposure, since PATH lookup does not include the CWD.
#
# --timeout comfortably exceeds the inner timeout=2 so a slow response is
# reported by the probe rather than killed by Docker mid-request.
HEALTHCHECK --interval=10s --timeout=5s --retries=3 --start-period=5s \
    CMD ["python3", "-I", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:15003/healthz', timeout=2).close()"]

CMD ["sh", "-c", "python3 /usr/local/bin/healthz.py & sleep infinity"]
