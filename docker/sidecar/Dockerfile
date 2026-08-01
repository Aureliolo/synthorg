# syntax=docker/dockerfile:1.26@sha256:ecfaec9ed6d810b56388c508f4121597bfbba70d41a6dfeee4d8cad5f295fc32
# =============================================================================
# SynthOrg Sidecar -- transparent network proxy for sandbox containers
# =============================================================================
# Multi-stage build: Go builder + apko-composed Wolfi base with iptables.
# The sidecar enforces allowed_hosts via dual-layer DNS + DNAT transparent
# proxy. It shares a network namespace with the sandbox container.
# =============================================================================

ARG BASE_IMAGE

FROM golang:1.26-alpine@sha256:0178a641fbb4858c5f1b48e34bdaabe0350a330a1b1149aabd498d0699ff5fb2 AS builder

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
