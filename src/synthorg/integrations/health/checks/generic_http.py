"""Generic HTTP health check."""

import asyncio
from datetime import UTC, datetime
from typing import Final, NamedTuple

import httpx

from synthorg.core.clock import Clock, SystemClock
from synthorg.integrations.connections.catalog import ConnectionCatalog
from synthorg.integrations.connections.http_vendor import (
    HttpVendorPreset,
    resolve_vendor,
)
from synthorg.integrations.connections.models import (
    Connection,
    ConnectionStatus,
    HealthReport,
)
from synthorg.integrations.errors import SecretRetrievalError
from synthorg.integrations.health.checks._http_verdicts import (
    deadline_report,
    network_report,
    report_response,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.integrations import (
    HEALTH_CHECK_FAILED,
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


class _ProbeTarget(NamedTuple):
    """The one request the probe should issue."""

    url: str
    preset: HttpVendorPreset | None


def _probe_target(connection: Connection) -> _ProbeTarget:
    """Resolve the URL the probe should use.

    A vendor with a free metadata endpoint is probed there; everyone else is
    probed at their own ``base_url``. Never with a payload: for a metered
    search API the payload IS the product, and sending one to colour a badge
    green spends the operator's quota on every probe, forever.

    Returns:
        The probe URL and the vendor contract that judges its response.
    """
    base = connection.base_url or ""
    preset = resolve_vendor(connection.metadata)
    if preset is None:
        return _ProbeTarget(base, None)
    return _ProbeTarget(preset.health_url or base, preset)


async def _issue_probe(
    client: httpx.AsyncClient,
    target: _ProbeTarget,
    headers: dict[str, str],
) -> httpx.Response:
    """Send the probe, carrying the credential and nothing else.

    A vendor-bound endpoint gets a GET: it is the method whose rejection was
    verified, and a HEAD tells us nothing because the evidence we need is in
    the error body. Everyone else keeps the cheap HEAD with a GET fallback.

    Returns:
        The response to judge.
    """
    if target.preset is not None:
        return await client.get(target.url, headers=headers)
    resp = await client.head(target.url, headers=headers)
    if resp.status_code in (_METHOD_NOT_ALLOWED, _NOT_IMPLEMENTED):
        return await client.get(target.url, headers=headers)
    return resp


_TIMEOUT: Final[float] = 10.0
_METHOD_NOT_ALLOWED: Final[int] = 405
_NOT_IMPLEMENTED: Final[int] = 501

_CREDENTIAL_BAD: Final[str] = "credential_misconfigured"
_STORE_UNAVAILABLE: Final[str] = "secret_store_unavailable"


class _AuthResolution(NamedTuple):
    """Resolved probe headers, or the reason they could not be resolved."""

    headers: dict[str, str] | None
    reason: str | None


class GenericHttpHealthCheck:
    """Health check against the connection's base URL.

    The default probe is a HEAD, falling back to GET if the server
    answers 405 or 501. A vendor preset overrides that shape when its
    endpoint needs one to answer at all: ``health_params`` forces a
    parameterised GET, ``health_body`` a POST carrying that JSON. A
    vendor-bound connection may therefore never send a HEAD.

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

    @staticmethod
    def _pinned_transport(
        validation: DnsValidationOk,
    ) -> httpx.AsyncBaseTransport | None:
        """Bind the TCP connect to the address the pre-flight validated.

        Without the pin, a malicious DNS server can rebind the hostname
        between the SSRF pre-flight and the request it was meant to clear.

        Returns:
            The pinned transport, or ``None`` for a literal-IP or
            allowlisted host, where the pre-flight accepted the address
            as-is and there is no second resolution to defend.
        """
        if not validation.resolved_ips:
            return None
        return PinnedDnsTransport(
            hostname=validation.hostname,
            ip=validation.resolved_ips[0],
        )

    async def _run_probe(
        self,
        connection: Connection,
        target: _ProbeTarget,
        headers: dict[str, str],
        transport: httpx.AsyncBaseTransport | None,
    ) -> HealthReport:
        """Issue the probe and judge whatever comes back.

        Returns:
            The verdict for the response, the deadline, or the network
            failure.
        """
        start = self._clock.monotonic()
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
                resp = await _issue_probe(client, target, headers)
        except TimeoutError:
            return deadline_report(
                connection, (self._clock.monotonic() - start) * 1000, _TIMEOUT
            )
        except httpx.HTTPError as exc:
            return network_report(
                connection, exc, (self._clock.monotonic() - start) * 1000
            )
        return report_response(
            connection, resp, (self._clock.monotonic() - start) * 1000, target.preset
        )

    async def check(self, connection: Connection) -> HealthReport:
        """Probe ``base_url`` in whichever shape the vendor preset declares.

        A HEAD with a GET fallback by default; a parameterised GET or a
        POST with a JSON body where the preset says the endpoint needs one.

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
        return await self._run_probe(
            connection,
            _probe_target(connection),
            headers,
            self._pinned_transport(validation),
        )
