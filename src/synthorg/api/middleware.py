"""Request middleware.

Provides ASGI middleware for request logging and path-aware
Content-Security-Policy headers.
"""

import time
from typing import Any, Final

from litestar import Request
from litestar.enums import ScopeType
from litestar.types import ASGIApp, Receive, Scope, Send  # noqa: TC002

from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_REQUEST_COMPLETED,
    API_REQUEST_STARTED,
)

logger = get_logger(__name__)

# Strict CSP for API routes — no inline scripts, self-origin only.
_API_CSP: Final[str] = "default-src 'self'; script-src 'self'"

# Relaxed CSP for /docs/ — Scalar UI loads resources from external origins.
# cdn.jsdelivr.net: JS bundle, CSS, fonts, source maps
# fonts.scalar.com: Scalar-hosted font files
# proxy.scalar.com: API proxy and registry features
_DOCS_CSP: Final[str] = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "img-src 'self' data: https://cdn.jsdelivr.net; "
    "font-src 'self' data: https://cdn.jsdelivr.net https://fonts.scalar.com; "
    "connect-src 'self' https://cdn.jsdelivr.net https://proxy.scalar.com"
)


class CSPMiddleware:
    """ASGI middleware that applies path-aware Content-Security-Policy.

    API routes get a strict policy (self-origin only). The ``/docs/``
    path gets a relaxed policy that allows Scalar UI resources from
    ``cdn.jsdelivr.net``, ``fonts.scalar.com``, and
    ``proxy.scalar.com``.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        """Inject the appropriate CSP header based on request path."""
        if scope["type"] != ScopeType.HTTP:
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")
        is_docs = path == "/docs" or path.startswith("/docs/")
        csp_value = _DOCS_CSP if is_docs else _API_CSP

        async def inject_csp(message: Any) -> None:
            if (
                isinstance(message, dict)
                and message.get("type") == "http.response.start"
            ):
                headers = list(message.get("headers", []))
                headers.append(
                    (b"content-security-policy", csp_value.encode()),
                )
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, inject_csp)


class RequestLoggingMiddleware:
    """ASGI middleware that logs request start and completion.

    Uses ``time.perf_counter()`` for high-resolution duration
    measurement.  Only logs HTTP requests (non-HTTP scopes like
    WebSocket and lifespan are passed through without logging).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        """Process an ASGI request, logging start and completion."""
        if scope["type"] != ScopeType.HTTP:
            await self.app(scope, receive, send)
            return

        request: Request[Any, Any, Any] = Request(scope)
        method = request.method
        path = str(request.url.path)

        logger.info(API_REQUEST_STARTED, method=method, path=path)
        start = time.perf_counter()

        status_code: int | None = None
        original_send = send

        async def capture_send(message: Any) -> None:
            nonlocal status_code
            if (
                isinstance(message, dict)
                and message.get("type") == "http.response.start"
            ):
                status_code = message.get("status", 500)
            await original_send(message)

        try:
            await self.app(scope, receive, capture_send)
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 2)
            if status_code is None:
                logger.warning(
                    API_REQUEST_COMPLETED,
                    method=method,
                    path=path,
                    status_code="unknown",
                    duration_ms=duration_ms,
                )
            else:
                logger.info(
                    API_REQUEST_COMPLETED,
                    method=method,
                    path=path,
                    status_code=status_code,
                    duration_ms=duration_ms,
                )
