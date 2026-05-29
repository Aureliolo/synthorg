"""DNS-rebinding-safe httpx transport.

Closes the DNS-rebinding TOCTOU window between
:func:`synthorg.tools.network_validator.validate_url_host` (which resolves and
validates a hostname's IPs) and the actual TCP connect: the transport pins the
validated IP at ``connect_tcp`` time while letting httpcore pass the original
hostname to ``start_tls`` separately, so TLS SNI and certificate verification
still use the hostname. No custom SSL context required, and HTTPS is handled
correctly.

Shared by the connection health check
(:mod:`synthorg.integrations.health.checks.generic_http`) and the governed
external-access tool's httpx provider so the proven pinning path is not
duplicated.
"""

import ssl
from typing import TYPE_CHECKING, cast, override

import httpcore
import httpx

if TYPE_CHECKING:
    from collections.abc import AsyncIterable, AsyncIterator, Iterable

    from httpcore._backends.base import SOCKET_OPTION


class PinnedDnsBackend(httpcore.AsyncNetworkBackend):
    """httpcore network backend that pins a hostname to a validated IP.

    Intercepts ``connect_tcp`` and substitutes the validated IP for the
    request's hostname before delegating to the inner backend. Because
    httpcore passes ``server_hostname`` to ``start_tls`` separately from the
    ``host`` arg of ``connect_tcp``, TLS SNI and certificate verification still
    use the original hostname.
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

    @override
    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        """Connect tcp.

        Returns:
            Result of type ``httpcore.AsyncNetworkStream``.
        """
        target = self._ip if host.lower() == self._hostname else host
        return await self._inner.connect_tcp(
            target,
            port,
            timeout=timeout,
            local_address=local_address,
            socket_options=socket_options,
        )

    @override
    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[SOCKET_OPTION] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        """Connect unix socket.

        Returns:
            Result of type ``httpcore.AsyncNetworkStream``.
        """
        return await self._inner.connect_unix_socket(
            path,
            timeout=timeout,
            socket_options=socket_options,
        )

    @override
    async def sleep(self, seconds: float) -> None:
        """Delegate the cooperative sleep to the wrapped backend."""
        await self._inner.sleep(seconds)


class PinnedDnsTransport(httpx.AsyncBaseTransport):
    """httpx transport whose underlying pool uses a hostname-pinned backend.

    Constructed only when there is a hostname-to-IP pinning to apply. Calls
    without a matching hostname fall through to the inner backend unchanged, so
    this transport is safe to use on any URL.
    """

    def __init__(self, *, hostname: str, ip: str) -> None:
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl.create_default_context(),
            network_backend=PinnedDnsBackend(
                httpcore.AnyIOBackend(),
                hostname=hostname,
                ip=ip,
            ),
        )

    @override
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        """Handle async request.

        Returns:
            Result of type ``httpx.Response``.

        Raises:
            TypeError: If an argument has an unexpected type.
        """
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
            stream=PinnedDnsResponseStream(
                cast("AsyncIterable[bytes]", resp.stream),
            ),
            extensions=resp.extensions,
        )

    @override
    async def aclose(self) -> None:
        """Close the underlying hostname-pinned connection pool."""
        await self._pool.aclose()


class PinnedDnsResponseStream(httpx.AsyncByteStream):
    """Forwarding wrapper that adapts an httpcore async stream to httpx."""

    def __init__(self, inner: AsyncIterable[bytes]) -> None:
        self._inner = inner

    @override
    async def __aiter__(self) -> AsyncIterator[bytes]:
        async for part in self._inner:
            yield part

    @override
    async def aclose(self) -> None:
        """Close the wrapped stream if it exposes an ``aclose`` coroutine."""
        aclose = getattr(self._inner, "aclose", None)
        if aclose is not None:
            await aclose()
