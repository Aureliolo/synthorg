"""CSRF protection middleware for cookie-based sessions.

Uses the double-submit cookie pattern: a non-HttpOnly CSRF
cookie is set alongside the session cookie.  JavaScript reads
the CSRF cookie and sends its value as the ``X-CSRF-Token``
header on mutating requests.  The middleware validates that the
header matches the cookie using constant-time comparison.

Only validates when a session cookie is present -- API key
requests (no cookie) skip CSRF entirely.
"""

import hmac as _hmac
import json
from http.cookies import SimpleCookie
from typing import Any

from litestar.types import ASGIApp, Receive, Scope, Send  # noqa: TC002

from synthorg.api.auth.config import AuthConfig  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_CSRF_SKIPPED,
)
from synthorg.observability.events.security import (
    SECURITY_CSRF_REJECTED,
)

logger = get_logger(__name__)

_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


class CsrfMiddleware:
    """CSRF validation middleware for cookie-based sessions.

    Only validates CSRF tokens on state-mutating methods
    (POST, PUT, PATCH, DELETE) when a session cookie is present
    in the request.  Requests authenticated via API key (no
    session cookie) skip validation entirely.

    Args:
        app: The next ASGI application in the stack.
        config: Auth configuration for cookie/header names.
        exempt_paths: Paths exempt from CSRF validation
            (e.g. login, setup).
    """

    def __init__(
        self,
        app: ASGIApp,
        *,
        config: AuthConfig,
        exempt_paths: frozenset[str] = frozenset(),
    ) -> None:
        self.app = app
        self._session_cookie_name = config.cookie_name
        self._csrf_cookie_name = config.csrf_cookie_name
        self._csrf_header_name = config.csrf_header_name
        self._exempt_paths = exempt_paths

    async def __call__(
        self,
        scope: Scope,
        receive: Receive,
        send: Send,
    ) -> None:
        """ASGI entrypoint.

        Args:
            scope: ASGI scope dict.
            receive: ASGI receive callable.
            send: ASGI send callable.
        """
        if scope.get("type") != "http":  # type: ignore[comparison-overlap]
            await self.app(scope, receive, send)
            return

        method = scope.get("method", "GET")
        if method in _SAFE_METHODS:
            await self.app(scope, receive, send)
            return

        path = (scope.get("path", "") or "").rstrip("/") or "/"
        if path in self._exempt_paths:
            await self.app(scope, receive, send)
            return

        # Parse cookies from raw headers
        cookies = _parse_cookies(scope.get("headers", []))

        session_cookie = cookies.get(self._session_cookie_name)
        if not session_cookie:
            # No session cookie -> not cookie-authenticated -> skip
            logger.debug(
                API_CSRF_SKIPPED,
                reason="no_session_cookie",
                path=path,
            )
            await self.app(scope, receive, send)
            return

        # Session cookie present -> validate CSRF token
        csrf_cookie = cookies.get(self._csrf_cookie_name)
        csrf_header = _get_header(scope.get("headers", []), self._csrf_header_name)

        if not csrf_cookie or not csrf_header:
            logger.warning(
                SECURITY_CSRF_REJECTED,
                reason="missing_csrf_token",
                path=path,
                has_cookie=bool(csrf_cookie),
                has_header=bool(csrf_header),
            )
            await _send_403(send)
            return

        if not _hmac.compare_digest(csrf_header, csrf_cookie):
            logger.warning(
                SECURITY_CSRF_REJECTED,
                reason="csrf_token_mismatch",
                path=path,
            )
            await _send_403(send)
            return

        await self.app(scope, receive, send)


async def _send_403(send: Send) -> None:
    """Send a 403 CSRF rejection response via raw ASGI.

    Raw ASGI middleware cannot raise Litestar exceptions because
    the Litestar exception handling pipeline has not yet wrapped
    the request.  Instead, send the error response directly.

    Args:
        send: ASGI send callable.
    """
    body = json.dumps(
        {
            "success": False,
            "data": None,
            "error": "CSRF token missing or invalid",
            "error_code": 1004,
            "error_category": "auth",
        }
    ).encode("utf-8")
    start: Any = {
        "type": "http.response.start",
        "status": 403,
        "headers": [
            (b"content-type", b"application/json"),
            (b"content-length", str(len(body)).encode("ascii")),
        ],
    }
    await send(start)
    body_msg: Any = {
        "type": "http.response.body",
        "body": body,
    }
    await send(body_msg)


def _parse_cookies(
    headers: list[tuple[bytes, bytes]] | Any,
) -> dict[str, str]:
    """Parse cookies from raw ASGI headers.

    Args:
        headers: List of (name, value) byte pairs.

    Returns:
        Dict mapping cookie names to values.
    """
    result: dict[str, str] = {}
    for name, value in headers:
        if name.lower() == b"cookie":
            try:
                morsel = SimpleCookie(value.decode("latin-1"))
            except Exception:  # noqa: S112
                # Malformed cookie header -- treat as absent.
                continue
            for key, m in morsel.items():
                result[key] = m.value
    return result


def _get_header(
    headers: list[tuple[bytes, bytes]] | Any,
    name: str,
) -> str | None:
    """Extract a header value by name (case-insensitive).

    Args:
        headers: List of (name, value) byte pairs.
        name: Header name to search for.

    Returns:
        Header value, or None if not found.
    """
    target = name.lower().encode("latin-1")
    for hdr_name, hdr_value in headers:
        if hdr_name.lower() == target:
            return hdr_value.decode("latin-1")
    return None


def create_csrf_middleware_class(
    config: AuthConfig,
    *,
    exempt_paths: frozenset[str] | None = None,
) -> type[CsrfMiddleware]:
    """Create a CSRF middleware class with baked-in configuration.

    Uses the same closure pattern as
    :func:`~synthorg.api.auth.middleware.create_auth_middleware_class`.

    Args:
        config: Auth configuration.
        exempt_paths: Paths exempt from CSRF validation.

    Returns:
        Middleware class ready for use in the Litestar stack.
    """
    paths = exempt_paths or frozenset()

    class ConfiguredCsrfMiddleware(CsrfMiddleware):
        """CSRF middleware with pre-configured settings."""

        def __init__(self, app: ASGIApp) -> None:
            super().__init__(app, config=config, exempt_paths=paths)

    return ConfiguredCsrfMiddleware
