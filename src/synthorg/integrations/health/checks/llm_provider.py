"""LLM-provider connection health check."""

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


class LlmProviderHealthCheck:
    """Reachability check for an LLM-provider connection.

    Probes the connection's ``base_url`` with a GET and treats any
    sub-500 response as ``HEALTHY`` (the endpoint is reachable; auth/path
    errors do not mean the provider is down). A 5xx, a network error, or
    an SSRF rejection is ``UNHEALTHY``. Providers that route through
    litellm's default endpoints carry no ``base_url`` and report
    ``UNKNOWN`` (there is nothing connection-local to probe).

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

    async def check(self, connection: Connection) -> HealthReport:
        """Probe the provider endpoint for reachability.

        Returns:
            ``HEALTHY`` for a sub-500 response, ``UNHEALTHY`` for a 5xx /
            network error / SSRF rejection, and ``UNKNOWN`` when the
            connection has no ``base_url`` to probe.
        """
        if not connection.base_url:
            return HealthReport(
                connection_name=connection.name,
                status=ConnectionStatus.UNKNOWN,
                error_detail="Provider routes via the litellm default endpoint; "
                "no base_url to probe",
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
