# syntax=docker/dockerfile:1

# =============================================================================
# SynthOrg Web — Non-root nginx container (CIS hardening applied via compose.yml)
# =============================================================================

FROM nginxinc/nginx-unprivileged:1.29.5-alpine@sha256:aec540f08f99df3c830549d5dd7bfaf63e01cbbb499e37400c5af9f8e8554e9f

LABEL org.opencontainers.image.title="synthorg-web" \
      org.opencontainers.image.description="SynthOrg web dashboard" \
      org.opencontainers.image.url="https://github.com/Aureliolo/synthorg" \
      org.opencontainers.image.source="https://github.com/Aureliolo/synthorg" \
      org.opencontainers.image.licenses="BUSL-1.1" \
      org.opencontainers.image.vendor="Aureliolo"

COPY web/nginx.conf /etc/nginx/conf.d/default.conf
COPY web/index.html web/style.css web/app.js /usr/share/nginx/html/

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --retries=3 --start-period=5s \
    CMD ["wget", "--spider", "--quiet", "http://localhost:8080/"]
