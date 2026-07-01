"""LLM-provider connection health check."""

from collections.abc import Awaitable, Callable
from typing import Final

import httpx

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
from synthorg.providers.health import ProviderHealthStatus, ProviderHealthSummary
from synthorg.tools._dns_pinning import PinnedDnsTransport
from synthorg.tools.network_validator import (
    DnsValidationOk,
    NetworkPolicy,
    validate_url_host,
)

logger = get_logger(__name__)

_TIMEOUT: Final[float] = 10.0
# An LLM endpoint that answers with ANY HTTP status is reachable -- a 401
# (missing key) or 404 (the base path has no handler) still proves the
# service is up. Only a 5xx is treated as the provider itself failing.
_SERVER_ERROR_THRESHOLD: Final[int] = 500

type ProviderHealthLookup = Callable[[str], Awaitable[ProviderHealthSummary | None]]

_SUMMARY_STATUS_MAP: Final[dict[ProviderHealthStatus, ConnectionStatus]] = {
    ProviderHealthStatus.UP: ConnectionStatus.HEALTHY,
    ProviderHealthStatus.DEGRADED: ConnectionStatus.DEGRADED,
    ProviderHealthStatus.DOWN: ConnectionStatus.UNHEALTHY,
}


class LlmProviderHealthCheck:
    """Health check for an LLM-provider connection.

    The providers subsystem owns provider health: when a
    :data:`ProviderHealthLookup` is bound (see
    ``bind_provider_health_lookup`` in the prober module), the check
    reports the provider health tracker's aggregated verdict -- the
    same source the Providers screen shows -- so the two surfaces can
    never disagree.

    Only when the tracker has no signal (no calls or probes in its
    24h window) does the check fall back to a connection-local
    reachability probe of ``base_url``: any sub-500 response is
    ``HEALTHY`` (auth/path errors still prove the service is up), a
    5xx / network error / SSRF rejection is ``UNHEALTHY``, and a
    connection with no ``base_url`` reports ``UNKNOWN``.

    Args:
        network_policy: SSRF policy applied to ``base_url`` before any
            request. ``None`` selects the fail-closed default.
        clock: Clock seam for latency measurement.
    """

    def __init__(
        self,
        *,
        network_policy: NetworkPolicy | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._network_policy = (
            network_policy if network_policy is not None else NetworkPolicy()
        )
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._provider_health: ProviderHealthLookup | None = None

    def bind_provider_health(self, lookup: ProviderHealthLookup) -> None:
        """Bind the provider-health lookup used before any URL probe.

        The lookup receives the CONNECTION name and returns the
        tracker's summary for the backing provider (or ``None`` when
        the connection does not map to a provider).
        """
        self._provider_health = lookup

    def _report_from_summary(
        self,
        connection_name: str,
        summary: ProviderHealthSummary,
        status: ConnectionStatus,
    ) -> HealthReport:
        detail = (
            f"{summary.error_rate_percent_24h:.1f}% error rate over "
            f"{summary.calls_last_24h} calls (24h)"
            if status is not ConnectionStatus.HEALTHY
            else None
        )
        return HealthReport(
            connection_name=connection_name,
            status=status,
            latency_ms=summary.avg_response_time_ms,
            error_detail=detail,
            checked_at=self._clock.now(),
        )

    async def check(self, connection: Connection) -> HealthReport:
        """Resolve provider health, preferring the provider tracker.

        Returns:
            The tracker-derived status when the providers subsystem has
            a verdict; otherwise the reachability-probe result
            (``HEALTHY`` for a sub-500 response, ``UNHEALTHY`` for a
            5xx / network error / SSRF rejection, ``UNKNOWN`` when the
            connection has no ``base_url``).
        """
        if self._provider_health is not None:
            summary = await self._provider_health(connection.name)
            if summary is not None:
                status = _SUMMARY_STATUS_MAP.get(summary.health_status)
                if status is not None:
                    return self._report_from_summary(connection.name, summary, status)
        if not connection.base_url:
            return HealthReport(
                connection_name=connection.name,
                status=ConnectionStatus.UNKNOWN,
                error_detail="No provider calls recorded yet and no base_url "
                "to probe; health is unknown until the provider is used",
                checked_at=self._clock.now(),
            )
        validation = await validate_url_host(connection.base_url, self._network_policy)
        if not isinstance(validation, DnsValidationOk):
            logger.warning(
                HEALTH_CHECK_FAILED,
                connection_name=connection.name,
                reason="ssrf_policy_rejected_base_url",
                validation_result=str(validation),
            )
            return HealthReport(
                connection_name=connection.name,
                status=ConnectionStatus.UNHEALTHY,
                error_detail=f"ssrf_policy_rejected: {validation}",
                checked_at=self._clock.now(),
            )
        # Pin the connect to the validated IP so DNS cannot rebind between
        # the SSRF pre-flight and the request.
        transport: httpx.AsyncBaseTransport | None = None
        if validation.resolved_ips:
            transport = PinnedDnsTransport(
                hostname=validation.hostname,
                ip=validation.resolved_ips[0],
            )
        start = self._clock.monotonic()
        try:
            async with httpx.AsyncClient(
                timeout=_TIMEOUT,
                follow_redirects=False,
                transport=transport,
            ) as client:
                resp = await client.get(connection.base_url)
            elapsed = (self._clock.monotonic() - start) * 1000
            if resp.status_code < _SERVER_ERROR_THRESHOLD:
                logger.info(
                    HEALTH_CHECK_PASSED,
                    connection_name=connection.name,
                    latency_ms=elapsed,
                )
                return HealthReport(
                    connection_name=connection.name,
                    status=ConnectionStatus.HEALTHY,
                    latency_ms=elapsed,
                    checked_at=self._clock.now(),
                )
            logger.warning(
                HEALTH_CHECK_FAILED,
                connection_name=connection.name,
                status_code=resp.status_code,
            )
            return HealthReport(
                connection_name=connection.name,
                status=ConnectionStatus.UNHEALTHY,
                latency_ms=elapsed,
                error_detail=f"HTTP {resp.status_code}",
                checked_at=self._clock.now(),
            )
        except httpx.HTTPError as exc:
            elapsed = (self._clock.monotonic() - start) * 1000
            scrubbed = safe_error_description(exc)
            logger.warning(
                HEALTH_CHECK_FAILED,
                connection_name=connection.name,
                error_type=type(exc).__name__,
                error=scrubbed,
            )
            return HealthReport(
                connection_name=connection.name,
                status=ConnectionStatus.UNHEALTHY,
                latency_ms=elapsed,
                error_detail=scrubbed,
                checked_at=self._clock.now(),
            )
