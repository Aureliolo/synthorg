# syntax=docker/dockerfile:1

# =============================================================================
# AI Company Web — CIS-hardened nginx container (non-root)
# =============================================================================

FROM nginxinc/nginx-unprivileged:1.29.5-alpine

LABEL org.opencontainers.image.title="ai-company-web" \
      org.opencontainers.image.description="AI Company web dashboard" \
      org.opencontainers.image.url="https://github.com/Aureliolo/ai-company" \
      org.opencontainers.image.source="https://github.com/Aureliolo/ai-company" \
      org.opencontainers.image.licenses="BUSL-1.1" \
      org.opencontainers.image.vendor="Aureliolo"

COPY web/nginx.conf /etc/nginx/conf.d/default.conf
COPY web/index.html /usr/share/nginx/html/

EXPOSE 8080

HEALTHCHECK --interval=10s --timeout=3s --retries=3 --start-period=5s \
    CMD ["wget", "--spider", "--quiet", "http://localhost:8080/"]
