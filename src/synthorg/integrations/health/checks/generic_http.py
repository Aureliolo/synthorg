"""Generic HTTP health check."""

from datetime import UTC, datetime
from typing import Final

import httpx

from synthorg.core.clock import Clock, SystemClock
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.connections.models import (
    Connection,
    ConnectionStatus,
    HealthReport,
)
from synthorg.integrations.errors import SecretRetrievalError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import (
    HEALTH_CHECK_FAILED,
    HEALTH_CHECK_PASSED,
)
from synthorg.tools._dns_pinning import PinnedDnsTransport
from synthorg.tools.external_api._credentials import build_auth_headers
from synthorg.tools.external_api.errors import ExternalApiCredentialError
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

    When a catalog is bound, the probe sends the connection's auth headers so a
    missing/invalid credential surfaces as UNHEALTHY rather than a false-green
    reachability pass. Without a catalog it is reachability-only.

    Args:
        catalog: Connection catalog used to resolve the connection's
            credentials for an authenticated probe. ``None`` (or unbound)
            falls back to an unauthenticated reachability check.
        network_policy: SSRF policy applied to the connection's
            ``base_url`` before the HTTP call. ``None`` selects the
            fail-closed default.
    """

    def __init__(
        self,
        catalog: ConnectionCatalog | None = None,
        *,
        network_policy: NetworkPolicy | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._catalog = catalog
        self._network_policy = (
            network_policy if network_policy is not None else NetworkPolicy()
        )
        self._clock: Clock = clock if clock is not None else SystemClock()

    def bind_catalog(self, catalog: ConnectionCatalog) -> None:
        """Bind the live catalog so the probe can send authenticated requests."""
        self._catalog = catalog

    async def _auth_headers(self, connection: Connection) -> dict[str, str] | None:
        """Resolve the connection's auth headers, or ``None`` if unresolvable.

        Returns:
            An empty dict for genuinely public endpoints (no auth material) or
            when no catalog is bound; the auth headers when credentials resolve;
            or ``None`` when credentials are configured-but-broken (so the caller
            reports UNHEALTHY rather than a false-green reachability pass).
        """
        if self._catalog is None:
            return {}
        try:
            credentials = await self._catalog.get_credentials(connection.name)
            return build_auth_headers(connection.auth_method, credentials)
        except ExternalApiCredentialError:
            return None
        except SecretRetrievalError:
            return None

    async def check(self, connection: Connection) -> HealthReport:
        """Execute a HEAD (or GET fallback) against ``base_url``.

        Returns:
            A ``HealthReport``: ``HEALTHY`` for an HTTP status < 400,
            ``UNHEALTHY`` for status >= 400, a network error, or an
            SSRF policy rejection, and ``UNKNOWN`` when no ``base_url``
            is configured.
        """
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
            # Prefix the consumer-facing detail so dashboards can
            # distinguish a security rejection from a generic network
            # failure (both currently surface as UNHEALTHY).
            return HealthReport(
                connection_name=connection.name,
                status=ConnectionStatus.UNHEALTHY,
                error_detail=f"ssrf_policy_rejected: {validation}",
                checked_at=datetime.now(UTC),
            )
        headers = await self._auth_headers(connection)
        if headers is None:
            logger.warning(
                HEALTH_CHECK_FAILED,
                connection_name=connection.name,
                reason="credential_resolution_failed",
            )
            return HealthReport(
                connection_name=connection.name,
                status=ConnectionStatus.UNHEALTHY,
                error_detail="credential resolution failed or incomplete",
                checked_at=datetime.now(UTC),
            )
        start = self._clock.monotonic()
        # Pin the TCP connect to the first validated IP returned by
        # ``validate_url_host`` so a malicious DNS server cannot rebind
        # the hostname between the SSRF pre-flight and the actual
        # request. ``resolved_ips`` is empty for literal-IP base URLs
        # and allowlisted hosts (where the pre-flight already accepted
        # the address as-is); in those cases we fall through to the
        # default httpx transport, which is identical to the prior
        # behaviour minus the rebinding window.
        transport: httpx.AsyncBaseTransport | None = None
        if validation.resolved_ips:
            transport = PinnedDnsTransport(
                hostname=validation.hostname,
                ip=validation.resolved_ips[0],
            )
        try:
            # ``follow_redirects=False`` is the httpx default but pinned
            # explicitly: the SSRF pre-flight only validates the initial
            # ``base_url``, so a 3xx redirect to an internal address
            # would otherwise bypass the gate.
            async with httpx.AsyncClient(
                timeout=_TIMEOUT,
                follow_redirects=False,
                transport=transport,
            ) as client:
                resp = await client.head(connection.base_url, headers=headers)
                if resp.status_code in (_METHOD_NOT_ALLOWED, _NOT_IMPLEMENTED):
                    resp = await client.get(connection.base_url, headers=headers)
            elapsed = (self._clock.monotonic() - start) * 1000
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
                checked_at=datetime.now(UTC),
            )
