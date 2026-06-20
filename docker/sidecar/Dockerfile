# syntax=docker/dockerfile:1.25@sha256:0adf442eae370b6087e08edc7c50b552d80ddf261576f4ebd6421006b2461f12
# =============================================================================
# SynthOrg Sidecar -- transparent network proxy for sandbox containers
# =============================================================================
# Multi-stage build: Go builder + apko-composed Wolfi base with iptables.
# The sidecar enforces allowed_hosts via dual-layer DNS + DNAT transparent
# proxy. It shares a network namespace with the sandbox container.
# =============================================================================

ARG BASE_IMAGE

FROM golang:1.26-alpine@sha256:3ad57304ad93bbec8548a0437ad9e06a455660655d9af011d58b993f6f615648 AS builder

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
