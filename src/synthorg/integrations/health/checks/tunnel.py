"""Tunnel-provider connection health check."""

from collections.abc import Awaitable, Callable

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.integrations.connections.models import (
    Connection,
    ConnectionStatus,
    HealthReport,
)
from synthorg.integrations.tunnel.protocol import TunnelProviderStatus
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import (
    HEALTH_CHECK_FAILED,
    HEALTH_CHECK_PASSED,
)

logger = get_logger(__name__)

type TunnelStatusLookup = Callable[[str], Awaitable[TunnelProviderStatus | None]]


class TunnelHealthCheck:
    """Health check for a ``tunnel-<provider>`` credential connection.

    The tunnel manager owns provider readiness: when a
    :data:`TunnelStatusLookup` is bound (see ``bind_tunnel_status_lookup``
    in the prober module), the check reports the same availability +
    credential verdict the dashboard's tunnel card shows. The lookup
    receives the CONNECTION name and resolves the backing provider's
    status (or ``None`` when the name maps to no known provider).

    Args:
        clock: Clock seam for report timestamps.
    """

    def __init__(self, *, clock: Clock | None = None) -> None:
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._status_lookup: TunnelStatusLookup | None = None

    def bind_tunnel_status_lookup(self, lookup: TunnelStatusLookup) -> None:
        """Bind the tunnel-manager status lookup after construction.

        The check registry is instantiated at import time before the
        tunnel manager exists, so it is injected at app startup.
        """
        self._status_lookup = lookup

    async def check(self, connection: Connection) -> HealthReport:
        """Resolve the backing provider's readiness from the manager.

        Returns:
            ``HEALTHY`` when the provider is available with its
            credential in place, ``UNHEALTHY`` when either is missing,
            and ``UNKNOWN`` when no manager is bound or the connection
            maps to no known provider.
        """
        now = self._clock.now()
        if self._status_lookup is None:
            logger.warning(
                HEALTH_CHECK_FAILED,
                connection_name=connection.name,
                error="tunnel manager not bound, cannot resolve status",
            )
            return HealthReport(
                connection_name=connection.name,
                status=ConnectionStatus.UNKNOWN,
                error_detail="tunnel manager not bound",
                checked_at=now,
            )
        try:
            status = await self._status_lookup(connection.name)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            scrubbed = safe_error_description(exc)
            logger.warning(
                HEALTH_CHECK_FAILED,
                connection_name=connection.name,
                reason="tunnel_status_lookup_failed",
                error_type=type(exc).__name__,
                error=scrubbed,
            )
            return HealthReport(
                connection_name=connection.name,
                status=ConnectionStatus.UNHEALTHY,
                error_detail=f"tunnel status lookup failed: {scrubbed}",
                checked_at=now,
            )
        if status is None:
            logger.warning(
                HEALTH_CHECK_FAILED,
                connection_name=connection.name,
                error="connection maps to no known tunnel provider",
            )
            return HealthReport(
                connection_name=connection.name,
                status=ConnectionStatus.UNKNOWN,
                error_detail="unknown tunnel provider",
                checked_at=now,
            )
        if status.available and status.credential_configured:
            logger.info(HEALTH_CHECK_PASSED, connection_name=connection.name)
            return HealthReport(
                connection_name=connection.name,
                status=ConnectionStatus.HEALTHY,
                checked_at=now,
            )
        if not status.available:
            detail = status.detail or "tunnel provider unavailable"
        else:
            detail = "credential stored but the provider reports none configured"
        logger.warning(
            HEALTH_CHECK_FAILED,
            connection_name=connection.name,
            provider=status.provider_id,
            available=status.available,
            credential_configured=status.credential_configured,
            error=detail,
        )
        return HealthReport(
            connection_name=connection.name,
            status=ConnectionStatus.UNHEALTHY,
            error_detail=detail,
            checked_at=now,
        )
