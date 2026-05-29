"""Database health check."""

from datetime import UTC, datetime

from synthorg.core.clock import Clock, SystemClock
from synthorg.integrations.connections.models import (
    Connection,
    ConnectionStatus,
    HealthReport,
)
from synthorg.observability import get_logger
from synthorg.observability.events.integrations import (
    HEALTH_CHECK_FAILED,
    HEALTH_CHECK_PASSED,
)

logger = get_logger(__name__)


class DatabaseHealthCheck:
    """Health check via lightweight query (placeholder).

    In production, this would use the connection's credentials to
    run ``SELECT 1`` against the database.  For now, it checks
    that required metadata fields are present.
    """

    def __init__(self, *, clock: Clock | None = None) -> None:
        self._clock = clock or SystemClock()

    async def check(self, connection: Connection) -> HealthReport:
        """Verify database connection metadata is valid.

        Returns:
            A ``HealthReport`` with ``HEALTHY`` status when both
            ``dialect`` and ``database`` metadata are present and
            non-blank, or ``UNKNOWN`` when either is missing.
        """
        start = self._clock.monotonic()
        raw_dialect = connection.metadata.get("dialect")
        raw_database = connection.metadata.get("database")
        dialect = raw_dialect.strip() if isinstance(raw_dialect, str) else ""
        database = raw_database.strip() if isinstance(raw_database, str) else ""
        elapsed = (self._clock.monotonic() - start) * 1000

        if not dialect or not database:
            logger.warning(
                HEALTH_CHECK_FAILED,
                connection_name=connection.name,
                error="missing dialect or database in metadata",
            )
            return HealthReport(
                connection_name=connection.name,
                status=ConnectionStatus.UNKNOWN,
                latency_ms=elapsed,
                error_detail="Missing dialect or database in metadata",
                checked_at=datetime.now(UTC),
            )

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
