"""Request logging middleware.

Logs every request start and completion with method, path, status
code, and duration using structured logging.
"""

import time
from typing import Any

from litestar import Request
from litestar.enums import ScopeType
from litestar.types import ASGIApp, Receive, Scope, Send  # noqa: TC002

from ai_company.observability import get_logger
from ai_company.observability.events.api import (
    API_REQUEST_COMPLETED,
    API_REQUEST_STARTED,
)

logger = get_logger(__name__)


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
            logger.info(
                API_REQUEST_COMPLETED,
                method=method,
                path=path,
                status_code=status_code if status_code is not None else 0,
                duration_ms=duration_ms,
            )
