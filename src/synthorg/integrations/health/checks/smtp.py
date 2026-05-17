"""SMTP health check."""

import asyncio
import smtplib
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

_TIMEOUT: Final[int] = 10


class SmtpHealthCheck:
    """Health check via SMTP EHLO."""

    def __init__(self, *, clock: Clock | None = None) -> None:
        self._clock: Clock = clock if clock is not None else SystemClock()

    async def check(self, connection: Connection) -> HealthReport:
        """Verify SMTP connectivity via EHLO."""
        start = self._clock.monotonic()
        try:
            result = await asyncio.to_thread(
                self._sync_check,
                connection,
            )
        except (smtplib.SMTPException, OSError, ValueError) as exc:
            # Only catch expected transport/config errors. Programming
            # bugs (TypeError, AttributeError, etc.) must propagate so
            # they are not silently reported as a transient SMTP
            # outage. ``ValueError`` is included to cover malformed
            # port metadata in ``_sync_check``.
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
                error_detail=str(exc),
                checked_at=datetime.now(UTC),
            )
        else:
            return result

    def _sync_check(self, connection: Connection) -> HealthReport:
        """Synchronous SMTP EHLO check (run in thread)."""
        start = self._clock.monotonic()
        # Explicitly validate the host/port metadata so malformed
        # config (``port=None``, ``port=[]``, etc.) raises a
        # ``ValueError`` the outer handler already translates into
        # an ``UNHEALTHY`` report, instead of leaking as ``TypeError``
        # out of ``int(...)``.
        host_raw = connection.metadata.get("host", "localhost")
        if not isinstance(host_raw, str) or not host_raw.strip():
            msg = "Invalid SMTP host metadata"
            raise ValueError(msg)
        host = host_raw.strip()

        port_raw = connection.metadata.get("port", "25")
        try:
            port = int(port_raw)
        except (TypeError, ValueError) as exc:
            msg = "Invalid SMTP port metadata"
            raise ValueError(msg) from exc
        if not 1 <= port <= 65535:  # noqa: PLR2004
            msg = "SMTP port out of range (must be 1..65535)"
            raise ValueError(msg)
        try:
            with smtplib.SMTP(host, port, timeout=_TIMEOUT) as smtp:
                code, _ = smtp.ehlo()
            elapsed = (self._clock.monotonic() - start) * 1000
            if 200 <= code < 300:  # noqa: PLR2004
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
            return HealthReport(
                connection_name=connection.name,
                status=ConnectionStatus.UNHEALTHY,
                latency_ms=elapsed,
                error_detail=f"SMTP EHLO returned {code}",
                checked_at=datetime.now(UTC),
            )
        except (smtplib.SMTPException, OSError) as exc:
            elapsed = (self._clock.monotonic() - start) * 1000
            return HealthReport(
                connection_name=connection.name,
                status=ConnectionStatus.UNHEALTHY,
                latency_ms=elapsed,
                error_detail=str(exc),
                checked_at=datetime.now(UTC),
            )
