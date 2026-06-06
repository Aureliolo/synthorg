# syntax=docker/dockerfile:1.24@sha256:87999aa3d42bdc6bea60565083ee17e86d1f3339802f543c0d03998580f9cb89
# =============================================================================
# SynthOrg Sidecar -- transparent network proxy for sandbox containers
# =============================================================================
# Multi-stage build: Go builder + apko-composed Wolfi base with iptables.
# The sidecar enforces allowed_hosts via dual-layer DNS + DNAT transparent
# proxy. It shares a network namespace with the sandbox container.
# =============================================================================

ARG BASE_IMAGE

FROM golang:1.26-alpine@sha256:f23e8b227fb4493eabe03bede4d5a32d04092da71962f1fb79b5f7d1e6c2a17f AS builder

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
