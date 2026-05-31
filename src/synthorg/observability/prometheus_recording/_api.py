# module-kind: code
"""HTTP request / error / client-disconnect recording."""

from synthorg.observability import get_logger
from synthorg.observability.events.metrics import (
    API_REQUEST_VALIDATION_FAILED,
    CLIENT_DISCONNECTED,
    METRICS_SCRAPE_FAILED,
)
from synthorg.observability.prometheus_labels import (
    VALID_API_ERROR_CATEGORIES,
    VALID_DISCONNECT_REASONS,
    VALID_DISCONNECT_TRANSPORTS,
    VALID_STATUS_CLASSES,
    require_label,
    require_non_negative,
    status_class,
)
from synthorg.observability.prometheus_recording._base import (
    _RecordingMetricsBase,
)

logger = get_logger(__name__)


class _ApiRecordingMixin(_RecordingMetricsBase):
    """HTTP request / error / client-disconnect recording."""

    def record_api_request(
        self,
        *,
        method: str,
        route: str,
        status_code: int,
        duration_sec: float,
    ) -> None:
        """Record an HTTP request handler's duration.

        Called from ``RequestLoggingMiddleware`` (``api/middleware.py``)
        once the response is fully constructed. ``route`` is a route
        template (e.g. ``"/agents/{agent_id}"``), never a raw path;
        the middleware resolves this via ``scope["route_handler"]``.

        Args:
            method: HTTP method (uppercase, e.g. ``"GET"``).
            route: Route template string; ``"__unmatched__"`` for 404s.
            status_code: Response status code (100-599).
            duration_sec: Wall-clock duration in seconds.

        Raises:
            ValueError: If ``status_code`` maps to a class outside
                1xx-5xx, or ``duration_sec`` is negative, NaN, or
                infinite.
        """
        sc = status_class(status_code)
        if sc not in VALID_STATUS_CLASSES:
            logger.warning(
                API_REQUEST_VALIDATION_FAILED,
                component="api_request",
                reason="invalid_status_code",
                method=method,
                route=route,
                status_code=status_code,
            )
            msg = f"record_api_request: invalid status_code {status_code!r}"
            raise ValueError(msg)
        require_non_negative("record_api_request: duration_sec", duration_sec)
        self._api_request_duration.labels(
            method=method,
            route=route,
            status_class=sc,
        ).observe(duration_sec)

    def record_api_error(
        self,
        *,
        category: str,
        status_code: int,
    ) -> None:
        """Increment the API error classification counter (4xx/5xx only).

        ``category`` tracks the RFC 9457 taxonomy
        (:data:`VALID_API_ERROR_CATEGORIES`, mirroring
        :class:`synthorg.api.errors.ErrorCategory`); 2xx/3xx status
        codes are rejected so the counter only covers real failures.

        Raises:
            ValueError: If ``category`` is not a known error category,
                or ``status_code`` does not map to ``"4xx"`` or ``"5xx"``.
        """
        require_label("api error category", category, VALID_API_ERROR_CATEGORIES)
        sc = status_class(status_code)
        if sc not in {"4xx", "5xx"}:
            logger.warning(
                METRICS_SCRAPE_FAILED,
                component="api_error",
                reason="non_error_status_code",
                category=category,
                status_code=status_code,
                mapped_class=sc,
            )
            msg = (
                f"record_api_error: status_code {status_code!r} is not 4xx/5xx"
                f" (mapped to {sc!r})"
            )
            raise ValueError(msg)
        self._api_error_classification.labels(
            category=category,
            status_class=sc,
        ).inc()

    def record_client_disconnect(
        self,
        *,
        transport: str,
        reason: str,
    ) -> None:
        """Increment the client-disconnect counter.

        Wired into SSE / WebSocket / MCP-stdio disconnect handlers so
        ops can alert on
        ``rate(synthorg_client_disconnects_total{reason="transport_error"}[5m])``.
        Both labels are bounded vocabularies so the time-series
        cardinality is fixed at 12 (transports x reasons).
        """
        require_label(
            "client disconnect transport",
            transport,
            VALID_DISCONNECT_TRANSPORTS,
        )
        require_label(
            "client disconnect reason",
            reason,
            VALID_DISCONNECT_REASONS,
        )
        self._client_disconnects.labels(
            transport=transport,
            reason=reason,
        ).inc()
        logger.info(
            CLIENT_DISCONNECTED,
            transport=transport,
            reason=reason,
        )
