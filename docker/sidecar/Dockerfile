# syntax=docker/dockerfile:1.26@sha256:ecfaec9ed6d810b56388c508f4121597bfbba70d41a6dfeee4d8cad5f295fc32
# =============================================================================
# SynthOrg Sidecar -- transparent network proxy for sandbox containers
# =============================================================================
# Multi-stage build: Go builder + apko-composed Wolfi base with iptables.
# The sidecar enforces allowed_hosts via dual-layer DNS + DNAT transparent
# proxy. It shares a network namespace with the sandbox container.
# =============================================================================

ARG BASE_IMAGE

# The digest, not the tag, decides which Go builds this: the official images
# set GOTOOLCHAIN=local, so the builder cannot fetch a newer toolchain to
# satisfy go.mod and a digest behind the `go` directive fails the build
# outright. That is the wanted posture for a hermetic image, and it means this
# digest and the directive move together.
FROM golang:1.26-alpine@sha256:70b46548e42db77e0966aaf3619fd068734dc6c77584d526b91126504fd95816 AS builder

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

# Enters as root and does not stay: Docker cannot deliver CAP_NET_ADMIN to a
# non-root process (execve computes the permitted set from file capabilities
# and the ambient set, both empty, so cap_add leaves only a bounding ceiling),
# and no-new-privileges rules out file capabilities as a way around it. The
# process installs its netfilter rules, then drops to uid 10002 for the whole
# of its serving life -- see internal/privdrop.
#
# DL3002 wants the last USER to be non-root, which is the right default and the
# wrong answer here: declaring 10002 is what made cap_add arrive empty and the
# sidecar die at startup on every host. The drop is enforced in the binary and
# pinned by tests, not by this line.
# hadolint ignore=DL3002
USER 0

# The binary probes itself: the base ships no HTTP client (its busybox carries
# no wget applet, and there is no curl), so a shell one-liner reports the
# container unhealthy for its whole life however well the endpoint answers.
HEALTHCHECK --interval=2s --timeout=2s --retries=3 --start-period=2s \
    CMD ["/usr/local/bin/sidecar", "-healthcheck"]

ENTRYPOINT ["/usr/local/bin/sidecar"]
