"""Generic HTTP health check."""

import asyncio
from datetime import UTC, datetime
from typing import Final, NamedTuple

import httpx

from synthorg.core.clock import Clock, SystemClock
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.connections.http_vendor import resolve_vendor
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
from synthorg.tools.external_api._credentials import build_connection_auth_headers
from synthorg.tools.external_api.errors import ExternalApiCredentialError
from synthorg.tools.network_validator import (
    DnsValidationOk,
    NetworkPolicy,
    validate_url_host,
)

logger = get_logger(__name__)


def _probe_target(connection: Connection) -> tuple[str, dict[str, str]]:
    """Resolve the URL and query the probe should use.

    A vendor preset can name a path and the parameters its API needs to
    answer at all: probing a search endpoint with no query returns a 4xx
    whatever the credential, which would report every correctly-configured
    connection as unhealthy. Both come from the code-defined preset, never
    from operator input, so composing them adds no SSRF surface beyond the
    host the pre-flight already validated.

    Returns:
        The probe URL and its query parameters (empty for a plain probe).
    """
    base = connection.base_url or ""
    preset = resolve_vendor(connection.metadata)
    if preset is None:
        return base, {}
    url = (
        f"{base.rstrip('/')}/{preset.health_path.lstrip('/')}"
        if preset.health_path
        else base
    )
    return url, dict(preset.health_params)


_TIMEOUT: Final[float] = 10.0
_ERROR_THRESHOLD: Final[int] = 400
_METHOD_NOT_ALLOWED: Final[int] = 405
_NOT_IMPLEMENTED: Final[int] = 501
_TOO_MANY_REQUESTS: Final[int] = 429

_CREDENTIAL_BAD: Final[str] = "credential_misconfigured"
_STORE_UNAVAILABLE: Final[str] = "secret_store_unavailable"


class _AuthResolution(NamedTuple):
    """Resolved probe headers, or the reason they could not be resolved."""

    headers: dict[str, str] | None
    reason: str | None


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

    async def _auth_headers(self, connection: Connection) -> _AuthResolution:
        """Resolve the connection's auth headers, or say why it could not.

        The two failure causes need to stay apart: a misconfigured credential
        is deterministic and the operator must re-enter it, whereas a secret
        backend that is down is transient and retries on its own. Reporting
        both as one verdict sends the operator to rotate working keys.

        Returns:
            Resolved headers (empty for a public endpoint or an unbound
            catalog), or a resolution naming the failure cause.
        """
        if self._catalog is None:
            return _AuthResolution({}, None)
        try:
            credentials = await self._catalog.get_credentials(connection.name)
            return _AuthResolution(
                build_connection_auth_headers(connection, credentials), None
            )
        except ExternalApiCredentialError as exc:
            reason = self._auth_failure(connection, exc, _CREDENTIAL_BAD)
            return _AuthResolution(None, reason)
        except SecretRetrievalError as exc:
            reason = self._auth_failure(connection, exc, _STORE_UNAVAILABLE)
            return _AuthResolution(None, reason)

    @staticmethod
    def _auth_failure(connection: Connection, exc: Exception, reason: str) -> str:
        """Log the credential failure and return the reason to report.

        Returns:
            The reason, so the log and the health report cannot disagree.
        """
        logger.warning(
            HEALTH_CHECK_FAILED,
            connection_name=connection.name,
            reason=reason,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return reason

    async def _preflight(
        self,
        connection: Connection,
    ) -> DnsValidationOk | HealthReport:
        """Clear the endpoint for probing, or report why it cannot be.

        Returns:
            The validated host on success; a finished ``HealthReport`` when
            the connection carries no endpoint or its endpoint fails the
            SSRF pre-flight.
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
        if isinstance(validation, DnsValidationOk):
            return validation
        logger.warning(
            HEALTH_CHECK_FAILED,
            connection_name=connection.name,
            reason="ssrf_policy_rejected_base_url",
        )
        # Prefix the consumer-facing detail so dashboards can distinguish a
        # security rejection from a generic network failure (both currently
        # surface as UNHEALTHY).
        return HealthReport(
            connection_name=connection.name,
            status=ConnectionStatus.UNHEALTHY,
            error_detail=f"ssrf_policy_rejected: {validation}",
            checked_at=datetime.now(UTC),
        )

    async def check(self, connection: Connection) -> HealthReport:
        """Execute a HEAD (or GET fallback) against ``base_url``.

        Returns:
            A ``HealthReport``: ``HEALTHY`` for an HTTP status < 400,
            ``UNHEALTHY`` for status >= 400, a network error, or an
            SSRF policy rejection, and ``UNKNOWN`` when no ``base_url``
            is configured.
        """
        validation = await self._preflight(connection)
        if isinstance(validation, HealthReport):
            return validation
        headers, failure = await self._auth_headers(connection)
        if headers is None:
            return HealthReport(
                connection_name=connection.name,
                status=ConnectionStatus.UNHEALTHY,
                error_detail=failure,
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
        url, params = _probe_target(connection)
        try:
            # ``follow_redirects=False`` is the httpx default but pinned
            # explicitly: the SSRF pre-flight only validates the initial
            # ``base_url``, so a 3xx redirect to an internal address
            # would otherwise bypass the gate.
            # httpx bounds each operation, not the call: a server dripping
            # bytes just under the read timeout keeps one request alive
            # indefinitely, and the HEAD-then-GET fallback would grant each
            # attempt the full budget. Every probe runs inside one prober
            # task group, so an unbounded wait here stalls the cycle for
            # every other connection too.
            async with (
                asyncio.timeout(_TIMEOUT),
                httpx.AsyncClient(
                    timeout=_TIMEOUT,
                    follow_redirects=False,
                    transport=transport,
                ) as client,
            ):
                if params:
                    # A vendor that declares probe parameters needs them to
                    # answer at all, and only a GET carries them meaningfully.
                    resp = await client.get(url, headers=headers, params=params)
                else:
                    resp = await client.head(url, headers=headers)
                    if resp.status_code in (_METHOD_NOT_ALLOWED, _NOT_IMPLEMENTED):
                        resp = await client.get(url, headers=headers)
            elapsed = (self._clock.monotonic() - start) * 1000
            return _report_response(connection, resp, elapsed)
        except TimeoutError:
            elapsed = (self._clock.monotonic() - start) * 1000
            logger.warning(
                HEALTH_CHECK_FAILED,
                connection_name=connection.name,
                reason="probe_deadline_exceeded",
                timeout_seconds=_TIMEOUT,
            )
            return HealthReport(
                connection_name=connection.name,
                status=ConnectionStatus.UNHEALTHY,
                latency_ms=elapsed,
                error_detail=f"probe exceeded {_TIMEOUT}s",
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


def _report_response(
    connection: Connection,
    resp: httpx.Response,
    elapsed: float,
) -> HealthReport:
    """Turn a probe response into a health verdict.

    Returns:
        ``HEALTHY`` below the error threshold, else ``UNHEALTHY`` carrying
        the status and, for a rate limit, its retry hint.
    """
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
    # A rate limit says nothing about whether the credential is valid, so it
    # is reported as its own cause rather than folded into the generic
    # failure detail an operator would read as one.
    retry_after = resp.headers.get("Retry-After") or ""
    rate_limited = resp.status_code == _TOO_MANY_REQUESTS
    logger.warning(
        HEALTH_CHECK_FAILED,
        connection_name=connection.name,
        status_code=resp.status_code,
        reason="rate_limited" if rate_limited else "http_error",
        retry_after=retry_after,
    )
    detail = f"HTTP {resp.status_code}"
    if rate_limited and retry_after:
        detail = f"{detail} (retry after {retry_after})"
    return HealthReport(
        connection_name=connection.name,
        status=ConnectionStatus.UNHEALTHY,
        latency_ms=elapsed,
        error_detail=detail,
        checked_at=datetime.now(UTC),
    )
