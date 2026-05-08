# syntax=docker/dockerfile:1.23@sha256:2780b5c3bab67f1f76c781860de469442999ed1a0d7992a5efdf2cffc0e3d769
# =============================================================================
# SynthOrg Sidecar -- transparent network proxy for sandbox containers
# =============================================================================
# Multi-stage build: Go builder + apko-composed Wolfi base with iptables.
# The sidecar enforces allowed_hosts via dual-layer DNS + DNAT transparent
# proxy. It shares a network namespace with the sandbox container.
# =============================================================================

ARG BASE_IMAGE

FROM golang:1.26-alpine@sha256:91eda9776261207ea25fd06b5b7fed8d397dd2c0a283e77f2ab6e91bfa71079d AS builder

WORKDIR /build
COPY docker/sidecar/go.mod ./
COPY docker/sidecar/ .
# Sidecar uses only stdlib -- no external dependencies, no go.sum needed.
RUN CGO_ENABLED=0 go build -ldflags="-s -w" -trimpath -o sidecar .

# -----------------------------------------------------------------------------
FROM ${BASE_IMAGE}

LABEL org.opencontainers.image.title="synthorg-sidecar" \
      org.opencontainers.image.description="SynthOrg transparent network proxy sidecar" \
      org.opencontainers.image.url="https://github.com/Aureliolo/synthorg" \
      org.opencontainers.image.source="https://github.com/Aureliolo/synthorg" \
      org.opencontainers.image.licenses="BUSL-1.1" \
      org.opencontainers.image.vendor="Aureliolo"

COPY --from=builder /build/sidecar /usr/local/bin/sidecar

# Non-root: iptables + SO_ORIGINAL_DST require CAP_NET_ADMIN, not UID 0.
# Container runtime grants NET_ADMIN via cap_add (cap_drop: ALL +
# cap_add: NET_ADMIN + read_only rootfs).
USER 10002

HEALTHCHECK --interval=2s --timeout=2s --retries=3 --start-period=2s \
    CMD wget -qO- http://localhost:15000/healthz || exit 1

ENTRYPOINT ["/usr/local/bin/sidecar"]
