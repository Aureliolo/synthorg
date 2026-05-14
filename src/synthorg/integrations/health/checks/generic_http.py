"""Generic HTTP health check."""

import time
from datetime import UTC, datetime
from typing import Final

import httpx

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
from synthorg.tools.network_validator import (
    DnsValidationOk,
    NetworkPolicy,
    validate_url_host,
)

logger = get_logger(__name__)

_TIMEOUT: Final[float] = 10.0
_ERROR_THRESHOLD: Final[int] = 400
_METHOD_NOT_ALLOWED: Final[int] = 405
_NOT_IMPLEMENTED: Final[int] = 501


class GenericHttpHealthCheck:
    """Health check via HTTP HEAD to the connection's base URL.

    Falls back to GET if the server returns 405 or 501 on HEAD.

    The configured ``base_url`` is validated against a
    :class:`NetworkPolicy` before any request is issued, so an operator
    cannot point a connection at ``http://127.0.0.1/`` (or a cloud
    metadata endpoint) and have the health prober ping it. The default
    policy is the fail-closed :class:`NetworkPolicy` (private IPs
    blocked, empty allowlist); callers needing internal hosts must
    supply a policy whose ``hostname_allowlist`` covers them.

    Args:
        network_policy: SSRF policy applied to the connection's
            ``base_url`` before the HTTP call. ``None`` selects the
            fail-closed default.
    """

    def __init__(self, *, network_policy: NetworkPolicy | None = None) -> None:
        self._network_policy = (
            network_policy if network_policy is not None else NetworkPolicy()
        )

    async def check(self, connection: Connection) -> HealthReport:
        """Execute a HEAD (or GET fallback) against ``base_url``."""
        if not connection.base_url:
            return HealthReport(
                connection_name=connection.name,
                status=ConnectionStatus.UNKNOWN,
                error_detail="No base_url configured",
                checked_at=datetime.now(UTC),
            )
        validation = await validate_url_host(
            connection.base_url,
            self._network_policy,
        )
        if not isinstance(validation, DnsValidationOk):
            logger.warning(
                HEALTH_CHECK_FAILED,
                connection_name=connection.name,
                reason="ssrf_policy_rejected_base_url",
            )
            return HealthReport(
                connection_name=connection.name,
                status=ConnectionStatus.UNHEALTHY,
                error_detail=validation,
                checked_at=datetime.now(UTC),
            )
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.head(connection.base_url)
                if resp.status_code in (_METHOD_NOT_ALLOWED, _NOT_IMPLEMENTED):
                    resp = await client.get(connection.base_url)
            elapsed = (time.monotonic() - start) * 1000
            if resp.status_code < _ERROR_THRESHOLD:
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
                checked_at=datetime.now(UTC),
            )
        except httpx.HTTPError as exc:
            elapsed = (time.monotonic() - start) * 1000
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
                checked_at=datetime.now(UTC),
            )
