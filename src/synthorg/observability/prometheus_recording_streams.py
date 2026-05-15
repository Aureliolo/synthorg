"""WebSocket lifetime + Postgres pool recording methods.

Split out of :mod:`synthorg.observability.prometheus_recording` so the
main module stays under the 800-line ceiling mandated by ``CLAUDE.md``.
The :class:`StreamRecordingMixin` is composed onto
:class:`PrometheusCollector` alongside the original ``RecordingMixin``;
the attributes it touches (``self._ws_connection_lifetime``, etc.) are
created by the collector's ``__init__`` and the public API surface for
callers (``collector.record_ws_*`` / ``collector.record_pg_pool_*``)
is unchanged.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from prometheus_client import Counter as PromCounter
    from prometheus_client import Gauge, Histogram

from synthorg.observability.prometheus_labels import (
    VALID_PG_BACKENDS,
    VALID_WS_REVALIDATION_OUTCOMES,
    VALID_WS_TRANSPORTS,
    require_finite,
    require_label,
    require_non_negative,
)


class StreamRecordingMixin:
    """WebSocket + Postgres pool push-time recording methods."""

    _ws_connection_lifetime: Histogram
    _ws_revalidation_outcomes: PromCounter
    _ws_active_connections: Gauge
    _pg_pool_size: Gauge
    _pg_pool_active_connections: Gauge
    _pg_pool_acquire_duration: Histogram
    _pg_pool_exhausted: PromCounter

    def record_ws_connection_lifetime(
        self,
        *,
        transport: str,
        duration_sec: float,
    ) -> None:
        """Observe a WebSocket connection's wall-clock lifetime.

        Called from the WS controller's close path; ``duration_sec``
        is the time between the auth-ok handshake and the close
        notification.
        """
        require_label("ws transport", transport, VALID_WS_TRANSPORTS)
        require_finite("record_ws_connection_lifetime: duration_sec", duration_sec)
        self._ws_connection_lifetime.labels(transport=transport).observe(
            duration_sec,
        )

    def record_ws_revalidation_outcome(self, *, outcome: str) -> None:
        """Increment the per-frame revalidation outcome counter."""
        require_label(
            "ws revalidation outcome",
            outcome,
            VALID_WS_REVALIDATION_OUTCOMES,
        )
        self._ws_revalidation_outcomes.labels(outcome=outcome).inc()

    def set_ws_active_connections(self, *, count: int) -> None:
        """Replace the gauge with the current connection count."""
        require_non_negative("set_ws_active_connections: count", count)
        self._ws_active_connections.set(count)

    def record_pg_pool_size(self, *, backend: str, size: int) -> None:
        """Replace the configured pool-size gauge."""
        require_label("pg backend", backend, VALID_PG_BACKENDS)
        require_non_negative("record_pg_pool_size: size", size)
        self._pg_pool_size.labels(backend=backend).set(size)

    def record_pg_pool_active(self, *, backend: str, active: int) -> None:
        """Replace the active-connection gauge."""
        require_label("pg backend", backend, VALID_PG_BACKENDS)
        require_non_negative("record_pg_pool_active: active", active)
        self._pg_pool_active_connections.labels(backend=backend).set(active)

    def record_pg_pool_acquire(
        self,
        *,
        backend: str,
        duration_sec: float,
    ) -> None:
        """Observe a connection-acquisition latency."""
        require_label("pg backend", backend, VALID_PG_BACKENDS)
        require_finite("record_pg_pool_acquire: duration_sec", duration_sec)
        self._pg_pool_acquire_duration.labels(backend=backend).observe(
            duration_sec,
        )

    def record_pg_pool_exhausted(self, *, backend: str) -> None:
        """Increment the pool-exhaustion counter."""
        require_label("pg backend", backend, VALID_PG_BACKENDS)
        self._pg_pool_exhausted.labels(backend=backend).inc()
