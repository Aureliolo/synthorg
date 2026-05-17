"""Generic HTTP health check."""

import ssl
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Final, cast

import httpcore
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
from synthorg.tools.network_validator import (
    DnsValidationOk,
    NetworkPolicy,
    validate_url_host,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterable, AsyncIterator, Iterable

    from httpcore._backends.base import SOCKET_OPTION

logger = get_logger(__name__)

_TIMEOUT: Final[float] = 10.0
_ERROR_THRESHOLD: Final[int] = 400
_METHOD_NOT_ALLOWED: Final[int] = 405
_NOT_IMPLEMENTED: Final[int] = 501


class _PinnedDnsBackend(httpcore.AsyncNetworkBackend):
    """httpcore network backend that pins a hostname to a validated IP.

    Closes the DNS-rebinding TOCTOU window between
    :func:`validate_url_host` and the actual TCP connect: the backend
    intercepts ``connect_tcp`` and substitutes the validated IP for the
    request's hostname before delegating to the inner backend. Because
    httpcore passes ``server_hostname`` to ``start_tls`` separately from
    the ``host`` arg of ``connect_tcp``, the TLS SNI and certificate
    verification still use the original hostname -- no custom SSL
    context required.
    """

    def __init__(
        self,
        inner: httpcore.AsyncNetworkBackend,
        *,
        hostname: str,
        ip: str,
    ) -> None:
        self._inner = inner
        self._hostname = hostname.lower()
        self._ip = ip

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,  # noqa: ASYNC109 -- AsyncNetworkBackend interface
        local_address: str | None = None,
        socket_options: Iterable[SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        target = self._ip if host.lower() == self._hostname else host
        return await self._inner.connect_tcp(
            target,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,  # noqa: ASYNC109 -- AsyncNetworkBackend interface
        socket_options: Iterable[SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        return await self._inner.connect_unix_socket(
            path,
            timeout=timeout,
            socket_options=socket_options,
        )

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class _PinnedDnsTransport(httpx.AsyncBaseTransport):
    """httpx transport whose underlying pool uses a hostname-pinned backend.

    Constructed only when there is a hostname-to-IP pinning to apply.
    Calls without a matching hostname fall through to the inner backend
    unchanged, so this transport is safe to use on any URL.
    """

    def __init__(self, *, hostname: str, ip: str) -> None:
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl.create_default_context(),
            network_backend=_PinnedDnsBackend(
                httpcore.AnyIOBackend(),
                hostname=hostname,
                ip=ip,
            ),
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if not isinstance(request.stream, httpx.AsyncByteStream):
            msg = "Pinned-DNS transport requires an async byte stream"
            raise TypeError(msg)
        req = httpcore.Request(
            method=request.method,
            url=httpcore.URL(
                scheme=request.url.raw_scheme,
                host=request.url.raw_host,
                port=request.url.port,
                target=request.url.raw_path,
            ),
            headers=request.headers.raw,
            content=request.stream,
            extensions=request.extensions,
        )
        resp = await self._pool.handle_async_request(req)
        return httpx.Response(
            status_code=resp.status,
            headers=resp.headers,
            stream=_PinnedDnsResponseStream(
                cast("AsyncIterable[bytes]", resp.stream),
            ),
            extensions=resp.extensions,
        )

    async def aclose(self) -> None:
        await self._pool.aclose()


class _PinnedDnsResponseStream(httpx.AsyncByteStream):
    """Forwarding wrapper that adapts an httpcore async stream to httpx."""

    def __init__(self, inner: AsyncIterable[bytes]) -> None:
        self._inner = inner

    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for part in self._inner:
            yield part

    async def aclose(self) -> None:
        aclose = getattr(self._inner, "aclose", None)
        if aclose is not None:
            await aclose()


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
            # Prefix the consumer-facing detail so dashboards can
            # distinguish a security rejection from a generic network
            # failure (both currently surface as UNHEALTHY).
            return HealthReport(
                connection_name=connection.name,
                status=ConnectionStatus.UNHEALTHY,
                error_detail=f"ssrf_policy_rejected: {validation}",
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
            transport = _PinnedDnsTransport(
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
                resp = await client.head(connection.base_url)
                if resp.status_code in (_METHOD_NOT_ALLOWED, _NOT_IMPLEMENTED):
                    resp = await client.get(connection.base_url)
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
