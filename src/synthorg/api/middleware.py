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
from collections.abc import Sequence
from contextlib import suppress
from types import MappingProxyType
from typing import Final

from litestar import Request
from litestar.datastructures import MutableScopeHeaders, State
from litestar.enums import ScopeType
from litestar.types import ASGIApp, Message, Receive, Scope, Send
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
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
from synthorg.observability.events.settings import SETTINGS_VALUE_RESOLVED
from synthorg.observability.state import ObservabilityStateSlice

_UNMATCHED_ROUTE: Final[str] = "__unmatched__"

# Healthcheck routes are polled by supervisors (Docker, k8s) on a
# fixed-interval schedule and produce thousands of identical
# ``api.request.started`` / ``api.request.completed`` records per
# day. They carry no operational signal beyond uptime, drown out
# real traffic in log queries, and inflate file-sink rotation churn.
# Suppress the structured request-log pair for these paths; the
# Prometheus duration metric and OpenTelemetry span still fire so
# operators retain aggregated latency visibility.
#
# Suffix match (not exact match) because the router prefix
# (``api.api_prefix``, default ``/api/v1``) is operator-configurable
# and the healthcheck controller paths are defined as ``/healthz``
# and ``/readyz`` without a leading prefix.
_HEALTHCHECK_PATH_SUFFIXES: Final[tuple[str, ...]] = ("/healthz", "/readyz")

logger = get_logger(__name__)


def _is_healthcheck_path(path: str) -> bool:
    """Return True for paths whose request-log pair should be skipped.

    Returns:
        ``True`` or ``False`` reflecting the condition.
    """
    return path.endswith(_HEALTHCHECK_PATH_SUFFIXES)


# ── Security headers ────────────────────────────────────────────
# Applied to every HTTP response via the before_send hook.

# Strict CSP for API routes -- no inline scripts, self-origin only.
_API_CSP: Final[str] = (
    "default-src 'self'; script-src 'self'; object-src 'none'; "
    "base-uri 'self'; frame-ancestors 'none'"
)

# Relaxed CSP for /docs/ -- Scalar UI loads resources from external origins.
# 'unsafe-inline' in script-src/style-src: required by Scalar UI which uses
# inline <script> and <style> elements.  Accepted risk -- /docs is read-only,
# unauthenticated, and serves no user-submitted content.
#
# The trusted-origin list is operator-tunable via the
# ``api.csp_docs_external_origins`` setting; defaults are the Scalar
# UI public CDN, fonts, and proxy hosts. ``set_docs_csp_origins`` is
# called once at startup with the resolved list, replacing the
# default-built ``_DOCS_CSP`` string.
_DOCS_CSP_DEFAULT_ORIGINS: Final[tuple[str, ...]] = (
    "https://cdn.jsdelivr.net",
    "https://fonts.scalar.com",
    "https://proxy.scalar.com",
)


def build_docs_csp(origins: Sequence[str]) -> str:
    """Build the relaxed Scalar UI CSP from a list of trusted origins.

    Origins are applied uniformly to ``script-src``, ``style-src``,
    ``img-src``, ``font-src`` and ``connect-src`` so operators can
    swap the public Scalar hosts for an internally-mirrored CDN with
    a single configuration change.

    An empty *origins* list raises ``ValueError`` rather than emit a
    malformed CSP with trailing whitespace before each ``;``. CSP
    parsers tolerate the trailing space but operators reading the
    header back would see an obviously broken policy; the
    ``ApiBridgeConfig`` validator is the right place to enforce
    non-empty (currently only validates pattern), so callers pass
    through the bridge-config-validated tuple.

    Args:
        origins: Origin URLs that Scalar UI assets and proxy requests
            may target. Must be non-empty. Each entry must already be
            a valid origin (scheme + host); ``ApiBridgeConfig``
            performs the per-entry validation.

    Returns:
        A CSP header value safe to assign to
        ``Content-Security-Policy`` for ``/docs/`` responses.

    Raises:
        ValueError: If *origins* is empty.
    """
    if not origins:
        msg = "build_docs_csp requires at least one trusted origin"
        raise ValueError(msg)
    joined = " ".join(origins)
    return (
        f"default-src 'self'; "
        f"script-src 'self' 'unsafe-inline' {joined}; "
        f"style-src 'self' 'unsafe-inline' {joined}; "
        f"img-src 'self' data: {joined}; "
        f"font-src 'self' data: {joined}; "
        f"connect-src 'self' {joined}; "
        f"object-src 'none'; "
        f"base-uri 'self'; "
        f"frame-ancestors 'none'"
    )


_DOCS_CSP: str = build_docs_csp(_DOCS_CSP_DEFAULT_ORIGINS)


def set_docs_csp_origins(origins: Sequence[str]) -> None:
    """Replace the docs CSP value with one built from *origins*.

    Called once at app startup after resolving
    ``api.csp_docs_external_origins`` through the settings service.
    Reset to the default list with ``_DOCS_CSP_DEFAULT_ORIGINS`` for
    test isolation.

    Calling this outside startup creates a brief eventual-consistency
    window for in-flight HTTP responses, since the docs ``before_send``
    hook reads the global at request time. The
    ``api.csp_docs_external_origins`` setting is marked
    ``restart_required=True`` precisely to keep this single-writer.
    """
    global _DOCS_CSP  # noqa: PLW0603 -- single-writer startup hook; tests reset via the same setter
    _DOCS_CSP = build_docs_csp(origins)
    logger.info(
        SETTINGS_VALUE_RESOLVED,
        namespace="api",
        key="csp_docs_external_origins",
        origins_count=len(origins),
    )


# Cache-Control for API data endpoints (named constant for test
# clarity; applied via _SECURITY_HEADERS).
#
# OWASP REST guidance + RFC 7234 best practice: combine no-store,
# no-cache, must-revalidate, and max-age=0 so legacy proxies and
# browsers that ignore one directive still skip caching. Every API
# response in this app is operator-authenticated and should never be
# cached, so the strongest possible value applies globally.
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
    }
)

# HTTP/1.0 Pragma directive -- applied to API paths but NOT to /docs
# (which serves cacheable, non-user-specific assets and explicitly
# overrides Cache-Control to ``public, max-age=300``). Co-locating
# Pragma with the API Cache-Control keeps the two cache hints
# consistent: a /docs response carries neither Pragma nor a no-cache
# directive, so legacy proxies still cache the SwaggerUI bundle.
_API_PRAGMA: Final[str] = "no-cache"


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
        # Defense-in-depth: even if an upstream layer set Pragma we
        # actively clear it on /docs so the no-cache hint can never
        # leak onto cacheable assets.
        with suppress(KeyError):
            del headers["Pragma"]
    else:
        headers["Pragma"] = _API_PRAGMA


def _log_request_started(method: str, path: str) -> None:
    """Log request start at INFO, skipping supervisor healthcheck paths."""
    if _is_healthcheck_path(path):
        return
    logger.info(API_REQUEST_STARTED, method=method, path=path)


def _log_request_completion(
    method: str,
    path: str,
    status_code: int | None,
    duration_ms: float,
) -> None:
    """Log request completion at the appropriate level."""
    if _is_healthcheck_path(path):
        return
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

    Returns:
        Resulting string.
    """
    template_hint = scope.get("path_template")
    if isinstance(template_hint, str) and template_hint:
        return template_hint
    handler: object = scope.get("route_handler")
    if handler is None:
        return _UNMATCHED_ROUTE
    paths = getattr(handler, "paths", None)
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

    Silent no-op when AppState or its collector is unavailable. A
    non-critical recording failure logs at WARNING and is dropped;
    interpreter-critical errors (``MemoryError`` / ``RecursionError``)
    propagate via ``reraise_critical``.
    """
    state: object = scope.get("state")
    if state is None:
        return
    app_state = state.get("app_state") if isinstance(state, dict) else None
    if app_state is None:
        return
    # Skip pre-response disconnects entirely rather than synthesising
    # a 5xx: those weren't errors the handler produced, and folding
    # them into ``status_class="5xx"`` would inflate SLO alarms.
    if status_code is None:
        return
    try:
        collector = app_state.slice(ObservabilityStateSlice).prometheus_collector
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        # Log the lookup failure so operators notice a metrics-
        # pipeline regression rather than seeing silent drop-offs.
        logger.warning(
            METRICS_RECORD_FAILED,
            component="api_request_duration",
            reason="collector_access_failed",
        )
        return
    if collector is None:
        return
    try:
        collector.record_api_request(
            method=method,
            route=_resolve_route_template(scope),
            status_code=status_code,
            duration_sec=duration_sec,
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            METRICS_RECORD_FAILED,
            component="api_request_duration",
        )


_tracer = trace.get_tracer(__name__)


class RequestLoggingMiddleware:
    """ASGI middleware that logs request start and completion.

    Uses ``time.perf_counter()`` for high-resolution duration
    measurement.  Only logs HTTP requests (non-HTTP scopes like
    WebSocket and lifespan are passed through without logging).

    Each HTTP request is also wrapped in an OpenTelemetry span
    (``http.request``) carrying OTel-semconv attributes
    (``http.request.method``, ``http.route``,
    ``http.response.status_code``) plus the ``synthorg.correlation_id``
    so distributed traces line up with the structured-log stream. When
    no tracer provider is configured (default), ``get_tracer`` returns
    a no-op tracer and the span is essentially free.
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

        request: Request[object, object, State] = Request(scope)
        method = request.method
        path = str(request.url.path)

        correlation_id = generate_correlation_id()
        bind_correlation_id(request_id=correlation_id)
        _log_request_started(method, path)
        start = time.perf_counter()

        status_code: int | None = None
        original_send = send

        async def capture_send(message: Message) -> None:
            """Run capture send."""
            nonlocal status_code
            if (
                isinstance(message, dict)
                and message.get("type") == "http.response.start"
            ):
                raw_status = message.get("status")
                if isinstance(raw_status, int):
                    status_code = raw_status
                else:
                    logger.warning(
                        API_ASGI_MISSING_STATUS,
                        type=message.get("type"),
                    )
                    status_code = 500
            await original_send(message)  # pyright: ignore[reportArgumentType]

        with _tracer.start_as_current_span(
            "http.request",
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            span.set_attribute("http.request.method", method)
            span.set_attribute("synthorg.correlation_id", correlation_id)
            try:
                await self.app(scope, receive, capture_send)
            except Exception as exc:
                reraise_critical(exc)
                # OTel's ``record_exception`` would serialise the full
                # traceback (including frame locals) into the span,
                # bypassing the structlog secret-log redaction the
                # rest of the codebase relies on. To keep the OTLP
                # transport on the same redaction posture as the
                # structlog sink, set OTel-semconv exception
                # attributes directly using the scrubbed description
                # and skip the traceback emission. See
                # ``docs/reference/sec-prompt-safety.md`` for the
                # transport-level redaction policy.
                span.set_attribute("exception.type", type(exc).__name__)
                span.set_attribute(
                    "exception.message",
                    safe_error_description(exc),
                )
                span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
                raise
            finally:
                span.set_attribute("http.route", _resolve_route_template(scope))
                if status_code is not None:
                    span.set_attribute("http.response.status_code", status_code)
                    if status_code >= 500:  # noqa: PLR2004
                        span.set_status(Status(StatusCode.ERROR))
                elapsed_sec = time.perf_counter() - start
                duration_ms = round(elapsed_sec * 1000, 2)
                _log_request_completion(method, path, status_code, duration_ms)
                _record_request_metric(scope, method, status_code, elapsed_sec)
                clear_correlation_ids()
