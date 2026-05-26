"""Shared health check helper.

Provides ``check_connection_health`` used by both the per-connection
endpoint on ``ConnectionsController`` and the aggregate endpoint on
``IntegrationHealthController``.
"""

from datetime import UTC, datetime

from synthorg.core.critical_errors import reraise_critical
from synthorg.integrations.connections.catalog import ConnectionCatalog  # noqa: TC001
from synthorg.integrations.connections.models import ConnectionStatus
from synthorg.integrations.health.models import HealthReport
from synthorg.integrations.health.prober import get_health_checker
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import HEALTH_CHECK_FAILED

logger = get_logger(__name__)


async def check_connection_health(
    catalog: ConnectionCatalog,
    name: str,
) -> HealthReport:
    """Run an on-demand health check for a single connection.

    Args:
        catalog: The connection catalog.
        name: Connection name.

    Returns:
        A ``HealthReport`` with the check result.

    Raises:
        ConnectionNotFoundError: If the connection does not exist.
    """
    conn = await catalog.get_or_raise(name)
    checker = get_health_checker(conn.connection_type)
    now = datetime.now(UTC)

    if checker is None:
        # Surface ``UNKNOWN`` instead of ``conn.health_status`` so an
        # on-demand caller never sees a stale persisted status (which
        # could otherwise report a long-dead integration as
        # ``HEALTHY``). The missing checker is logged so operators can
        # register one.
        logger.warning(
            HEALTH_CHECK_FAILED,
            connection_name=name,
            connection_type=(
                conn.connection_type.value
                if hasattr(conn.connection_type, "value")
                else str(conn.connection_type)
            ),
            error="no health checker registered for this type",
        )
        return HealthReport(
            connection_name=conn.name,
            status=ConnectionStatus.UNKNOWN,
            error_detail="No health checker for this type",
            checked_at=now,
        )

    try:
        return await checker.check(conn)
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            HEALTH_CHECK_FAILED,
            connection_name=name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return HealthReport(
            connection_name=conn.name,
            status=ConnectionStatus.UNHEALTHY,
            error_detail=str(exc),
            checked_at=now,
        )
