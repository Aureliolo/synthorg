"""Database health check."""

import asyncio
import contextlib
from datetime import UTC, datetime
from typing import Final

from synthorg.core.clock import Clock, SystemClock
from synthorg.integrations.connections.models import (
    Connection,
    ConnectionStatus,
    HealthReport,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import (
    HEALTH_CHECK_FAILED,
    HEALTH_CHECK_PASSED,
)

logger = get_logger(__name__)

_TCP_TIMEOUT_SECONDS: Final[float] = 10.0
_MAX_PORT: Final[int] = 65535


class DatabaseHealthCheck:
    """Health check via a TCP-reachability probe to the database endpoint.

    A genuine driver-level ``SELECT 1`` cannot run here without
    importing a database driver, which the Persistence Boundary
    forbids outside ``persistence/`` and which would also tie this
    integration check to a single dialect. Instead the check opens a
    TCP connection to the configured ``host``/``port`` -- a real,
    dialect-agnostic liveness signal -- and reports ``UNKNOWN`` only
    when the endpoint coordinates are missing, rather than falsely
    reporting ``HEALTHY`` from metadata presence alone.
    """

    def __init__(self, *, clock: Clock | None = None) -> None:
        self._clock: Clock = clock if clock is not None else SystemClock()

    async def check(self, connection: Connection) -> HealthReport:
        """Probe database reachability over TCP.

        Returns:
            A ``HealthReport``: ``HEALTHY`` when the ``host``/``port``
            endpoint accepts a TCP connection, ``UNHEALTHY`` when the
            connection attempt fails or times out, and ``UNKNOWN`` when
            the endpoint coordinates are absent or malformed.
        """
        start = self._clock.monotonic()
        host, port, coordinate_error = self._resolve_endpoint(connection)
        if coordinate_error is not None:
            elapsed = (self._clock.monotonic() - start) * 1000
            logger.warning(
                HEALTH_CHECK_FAILED,
                connection_name=connection.name,
                error=coordinate_error,
            )
            return HealthReport(
                connection_name=connection.name,
                status=ConnectionStatus.UNKNOWN,
                latency_ms=elapsed,
                error_detail=coordinate_error,
                checked_at=datetime.now(UTC),
            )

        try:
            await self._probe(host, port)
        except (OSError, TimeoutError) as exc:
            elapsed = (self._clock.monotonic() - start) * 1000
            logger.warning(
                HEALTH_CHECK_FAILED,
                connection_name=connection.name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return HealthReport(
                connection_name=connection.name,
                status=ConnectionStatus.UNHEALTHY,
                latency_ms=elapsed,
                error_detail=safe_error_description(exc),
                checked_at=datetime.now(UTC),
            )

        elapsed = (self._clock.monotonic() - start) * 1000
        logger.info(
            HEALTH_CHECK_PASSED,
            connection_name=connection.name,
            latency_ms=elapsed,
        )
        return HealthReport(
            connection_name=connection.name,
            status=ConnectionStatus.HEALTHY,
            latency_ms=elapsed,
            checked_at=datetime.now(UTC),
        )

    def _resolve_endpoint(
        self,
        connection: Connection,
    ) -> tuple[str, int, str | None]:
        """Resolve and validate the ``host``/``port`` probe coordinates.

        Returns:
            A ``(host, port, error)`` triple. ``error`` is ``None`` when
            both coordinates are valid; otherwise ``host``/``port`` are
            placeholders and ``error`` describes the missing/malformed
            metadata.
        """
        raw_host = connection.metadata.get("host")
        host = raw_host.strip() if isinstance(raw_host, str) else ""
        if not host:
            return "", 0, "Missing host in metadata for connectivity probe"

        raw_port = connection.metadata.get("port", "")
        try:
            port = int(raw_port)
        except TypeError, ValueError:
            return host, 0, "Missing or invalid port in metadata"
        if not 1 <= port <= _MAX_PORT:
            return host, 0, "Database port out of range (must be 1..65535)"
        return host, port, None

    async def _probe(self, host: str, port: int) -> None:
        """Open and immediately close a TCP connection to ``host:port``.

        Raises:
            OSError: If the TCP connection cannot be established.
            TimeoutError: If the connection attempt exceeds the budget.
        """
        async with asyncio.timeout(_TCP_TIMEOUT_SECONDS):
            _, writer = await asyncio.open_connection(host, port)
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()
