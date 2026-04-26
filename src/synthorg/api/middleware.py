"""Request middleware and before-send hooks.

Provides ASGI middleware for request logging, and a ``before_send``
hook that injects security headers (CSP, CORP, HSTS, Cache-Control,
etc.) into **every** HTTP response -- including exception-handler and
unmatched-route (404/405) responses.

Why ``before_send`` instead of ASGI middleware?
Litestar's ``before_send`` hook wraps the ASGI ``send`` callback at
the outermost layer (before the middleware stack), so it fires for
all responses.  By contrast, user-defined ASGI middleware only runs
for matched routes -- 404 and 405 responses from the router bypass it.
"""

import time
from types import MappingProxyType
from typing import Any, Final

from litestar import Request
from litestar.datastructures import MutableScopeHeaders
from litestar.enums import ScopeType
from litestar.types import ASGIApp, Message, Receive, Scope, Send  # noqa: TC002

from synthorg.observability import get_logger
from synthorg.observability.correlation import (
    bind_correlation_id,
    clear_correlation_ids,
    generate_correlation_id,
)
from synthorg.observability.events.api import (
    API_ASGI_MISSING_STATUS,
    API_REQUEST_COMPLETED,
    API_REQUEST_STARTED,
)
from synthorg.observability.events.metrics import METRICS_RECORD_FAILED

_UNMATCHED_ROUTE: Final[str] = "__unmatched__"

logger = get_logger(__name__)

# ── Security headers ────────────────────────────────────────────
# Applied to every HTTP response via the before_send hook.

# Strict CSP for API routes -- no inline scripts, self-origin only.
_API_CSP: Final[str] = (
    "default-src 'self'; script-src 'self'; object-src 'none'; "
    "base-uri 'self'; frame-ancestors 'none'"
)

# Relaxed CSP for /docs/ -- Scalar UI loads resources from external origins.
# cdn.jsdelivr.net: JS bundle, CSS, fonts, source maps
# fonts.scalar.com: Scalar-hosted font files
# proxy.scalar.com: API proxy and registry features
# 'unsafe-inline' in script-src/style-src: required by Scalar UI which uses
# inline <script> and <style> elements.  Accepted risk -- /docs is read-only,
# unauthenticated, and serves no user-submitted content.
_DOCS_CSP: Final[str] = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
    "img-src 'self' data: https://cdn.jsdelivr.net; "
    "font-src 'self' data: https://cdn.jsdelivr.net https://fonts.scalar.com; "
    "connect-src 'self' https://cdn.jsdelivr.net https://proxy.scalar.com; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'"
)

# Cache-Control for API data endpoints (named constant for test
# clarity; applied via _SECURITY_HEADERS).
#
# OWASP REST guidance + RFC 7234 best practice: combine no-store,
# no-cache, must-revalidate, and max-age=0 so legacy proxies and
# browsers that ignore one directive still skip caching. Every API
# response in this app is operator-authenticated and should never be
# cached, so the strongest possible value applies globally (#1599).
_API_CACHE_CONTROL: Final[str] = "no-store, no-cache, must-revalidate, max-age=0"

# Cache-Control for documentation paths -- OpenAPI spec and Scalar UI
# are public, unauthenticated, non-user-specific content safe for
# brief caching.  public: shared caches (proxies) may store;
# max-age=300: fresh for 5 minutes, then stale (cache should revalidate).
_DOCS_CACHE_CONTROL: Final[str] = "public, max-age=300"

# Static security headers (path-independent, immutable at runtime).
_SECURITY_HEADERS: Final[MappingProxyType[str, str]] = MappingProxyType(
    {
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Strict-Transport-Security": "max-age=63072000; includeSubDomains",
        "Permissions-Policy": "geolocation=(), camera=(), microphone=()",
        "Cross-Origin-Resource-Policy": "same-origin",
        "Cross-Origin-Opener-Policy": "same-origin",
        "Cache-Control": _API_CACHE_CONTROL,
        # HTTP/1.0 Pragma directive -- belt-and-braces alongside
        # Cache-Control: no-store for clients that only honour Pragma.
        "Pragma": "no-cache",
    }
)


async def security_headers_hook(message: Message, scope: Scope) -> None:
    """Inject security headers into every HTTP response.

    Registered as a Litestar ``before_send`` hook so it fires for
    **all** HTTP responses -- successful, exception-handler, and
    router-level 404/405.

    Adds static security headers (CORP, HSTS, X-Content-Type-Options,
    etc.) and path-aware Content-Security-Policy (strict for API,
    relaxed for ``/docs/`` to allow Scalar UI resources) and
    Cache-Control (``no-store`` for API, ``public, max-age=300``
    for ``/docs/`` since it serves public, non-user-specific content).

    Uses ``__setitem__`` (not ``add``) so that if any handler or
    middleware already set a header, the known-good value overwrites
    it rather than creating a duplicate.

    Args:
        message: ASGI message dict (only ``http.response.start``
            is processed).
        scope: ASGI connection scope.
    """
    if scope.get("type") != ScopeType.HTTP:
        return
    if message.get("type") != "http.response.start":
        return

    headers = MutableScopeHeaders.from_message(message)

    # Static security headers -- overwrite to prevent duplicates.
    for name, value in _SECURITY_HEADERS.items():
        headers[name] = value

    # Path-aware headers
    path: str = scope.get("path", "")
    is_docs = path == "/docs" or path.startswith("/docs/")
    headers["Content-Security-Policy"] = _DOCS_CSP if is_docs else _API_CSP

    # Relax COOP for /docs -- Scalar UI may open cross-origin popups
    # for OAuth/API proxy features via proxy.scalar.com.
    # same-origin-allow-popups: allows the page to open popups but
    # blocks cross-origin pages from retaining an opener reference,
    # preventing XS-Leak side-channel attacks via window.opener.
    # Allow brief caching for docs -- public, non-user-specific content.
    if is_docs:
        headers["Cross-Origin-Opener-Policy"] = "same-origin-allow-popups"
        headers["Cache-Control"] = _DOCS_CACHE_CONTROL


def _log_request_completion(
    method: str,
    path: str,
    status_code: int | None,
    duration_ms: float,
) -> None:
    """Log request completion at the appropriate level."""
    if status_code is None:
        logger.warning(
            API_REQUEST_COMPLETED,
            method=method,
            path=path,
            status_code=0,
            status_code_captured=False,
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


def _resolve_route_template(scope: Scope) -> str:
    """Resolve the route template from a post-routing ASGI scope.

    Prefers ``scope["path_template"]`` which Litestar populates with
    the exact template that matched this request; falls back to
    ``sorted(handler.paths)[0]`` for older router versions. Returns
    :data:`_UNMATCHED_ROUTE` when no handler was reached (404,
    method-not-allowed, exceptions raised pre-routing).
    """
    template_hint = scope.get("path_template")
    if isinstance(template_hint, str) and template_hint:
        return template_hint
    handler: Any = scope.get("route_handler")
    if handler is None:
        return _UNMATCHED_ROUTE
    paths: Any = getattr(handler, "paths", None)
    if not paths:
        return _UNMATCHED_ROUTE
    # ``paths`` is a frozenset of route templates for the handler.
    # Sort for determinism when a handler registers multiple paths.
    template: str = sorted(paths)[0]
    return template


def _record_request_metric(
    scope: Scope,
    method: str,
    status_code: int | None,
    duration_sec: float,
) -> None:
    """Push api_request_duration to the collector stored in AppState.

    Silent no-op when AppState or its collector is unavailable; any
    recording failure logs at WARNING but does not propagate.
    """
    state: Any = scope.get("state")
    if state is None:
        return
    app_state: Any = state.get("app_state") if isinstance(state, dict) else None
    if app_state is None or not getattr(app_state, "has_prometheus_collector", False):
        return
    # Skip pre-response disconnects entirely rather than synthesising
    # a 5xx: those weren't errors the handler produced, and folding
    # them into ``status_class="5xx"`` would inflate SLO alarms.
    if status_code is None:
        return
    try:
        collector = app_state.prometheus_collector
    except MemoryError, RecursionError:
        raise
    except Exception:
        # Log the lookup failure so operators notice a metrics-
        # pipeline regression rather than seeing silent drop-offs.
        logger.warning(
            METRICS_RECORD_FAILED,
            component="api_request_duration",
            reason="collector_access_failed",
            exc_info=True,
        )
        return
    try:
        collector.record_api_request(
            method=method,
            route=_resolve_route_template(scope),
            status_code=status_code,
            duration_sec=duration_sec,
        )
    except MemoryError, RecursionError:
        raise
    except Exception:
        logger.warning(
            METRICS_RECORD_FAILED,
            component="api_request_duration",
            exc_info=True,
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

        bind_correlation_id(request_id=generate_correlation_id())
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
                raw_status = message.get("status")
                if raw_status is None:
                    logger.warning(
                        API_ASGI_MISSING_STATUS,
                        type=message.get("type"),
                    )
                    status_code = 500
                else:
                    status_code = raw_status
            await original_send(message)  # pyright: ignore[reportArgumentType]

        try:
            await self.app(scope, receive, capture_send)
        finally:
            elapsed_sec = time.perf_counter() - start
            duration_ms = round(elapsed_sec * 1000, 2)
            _log_request_completion(method, path, status_code, duration_ms)
            _record_request_metric(scope, method, status_code, elapsed_sec)
            clear_correlation_ids()
