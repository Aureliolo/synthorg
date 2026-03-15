"""Request middleware and before-send hooks.

Provides ASGI middleware for request logging, and a ``before_send``
hook that injects security headers (CSP, CORP, HSTS, etc.) into
**every** HTTP response — including exception-handler and
unmatched-route (404/405) responses.

Why ``before_send`` instead of ASGI middleware?
Litestar's ``before_send`` hook wraps the ASGI ``send`` callback at
the outermost layer (before the middleware stack), so it fires for
all responses.  By contrast, user-defined ASGI middleware only runs
for matched routes — 404 and 405 responses from the router bypass it.
"""

import time
from typing import Any, Final

from litestar import Request
from litestar.datastructures import MutableScopeHeaders
from litestar.enums import ScopeType
from litestar.types import ASGIApp, Message, Receive, Scope, Send  # noqa: TC002

from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_REQUEST_COMPLETED,
    API_REQUEST_STARTED,
)

logger = get_logger(__name__)

# ── Security headers ────────────────────────────────────────────
# Applied to every HTTP response via the before_send hook.

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

# Static security headers (path-independent).
_SECURITY_HEADERS: Final[dict[str, str]] = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
    "Permissions-Policy": "geolocation=(), camera=(), microphone=()",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Cache-Control": "no-store",
}


async def security_headers_hook(message: Message, scope: Scope) -> None:
    """Inject security headers into every HTTP response.

    Registered as a Litestar ``before_send`` hook so it fires for
    **all** HTTP responses — successful, exception-handler, and
    router-level 404/405.

    Adds static security headers (CORP, HSTS, X-Content-Type-Options,
    etc.) and a path-aware Content-Security-Policy (strict for API,
    relaxed for ``/docs/`` to allow Scalar UI resources).
    """
    if scope.get("type") != ScopeType.HTTP:
        return
    if message.get("type") != "http.response.start":
        return

    headers = MutableScopeHeaders.from_message(message)

    # Static security headers
    for name, value in _SECURITY_HEADERS.items():
        headers.add(name, value)

    # Path-aware CSP
    path: str = scope.get("path", "")
    is_docs = path == "/docs" or path.startswith("/docs/")
    headers.add(
        "Content-Security-Policy",
        _DOCS_CSP if is_docs else _API_CSP,
    )


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
