# syntax=docker/dockerfile:1

# =============================================================================
# SynthOrg Web — Multi-stage build: Node builder → nginx runtime
# CIS hardening applied via compose.yml (no-new-privileges, drop caps, etc.)
# =============================================================================

# Stage 1: Build Vue dashboard
FROM node:22-alpine AS builder
WORKDIR /app
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# Stage 2: Serve with nginx
FROM nginxinc/nginx-unprivileged:1.29.5-alpine@sha256:aec540f08f99df3c830549d5dd7bfaf63e01cbbb499e37400c5af9f8e8554e9f

LABEL org.opencontainers.image.title="synthorg-web" \
      org.opencontainers.image.description="SynthOrg web dashboard" \
      org.opencontainers.image.url="https://github.com/Aureliolo/synthorg" \
      org.opencontainers.image.source="https://github.com/Aureliolo/synthorg" \
      org.opencontainers.image.licenses="BUSL-1.1" \
      org.opencontainers.image.vendor="Aureliolo"

COPY web/nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=builder /app/dist/ /usr/share/nginx/html/

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --retries=3 --start-period=5s \
    CMD ["wget", "--spider", "--quiet", "http://localhost:8080/"]
