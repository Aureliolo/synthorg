# module-kind: code
"""An httpx client that follows the process-wide TLS trust snapshot.

An ``httpx.AsyncClient`` fixes its TLS configuration when it is built, so
a client cached for the life of a long-running service keeps answering
over the trust it was born with. The direction that matters is
verify-off to verify-on: holding the old client means the traffic an
operator has just asked to be verified is the traffic still skipping it.

Rebuilding on a trust change is only half of it. The displaced client
owns a connection pool and its sockets, which leak unless something
closes it, and closing it while a request is still riding on it fails
that request. So a displaced client is *retired* rather than dropped, and
closed once the last caller that borrowed it has returned.
"""

import ssl
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import httpx

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.tls_trust import httpx_verify, trust_revision
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.security import SECURITY_TLS_CLIENT_CLOSE_FAILED

logger = get_logger(__name__)


@runtime_checkable
class ClientBuilder(Protocol):
    """Builds a client against a caller-supplied TLS trust argument."""

    def __call__(self, *, verify: ssl.SSLContext | bool) -> httpx.AsyncClient:
        """Return a client configured to verify as instructed."""
        ...


@dataclass(slots=True)
class _Lease:
    """One built client plus the borrowers currently riding on it."""

    client: httpx.AsyncClient
    revision: int
    borrowers: int = field(default=0)


async def _close_all(leases: list[_Lease]) -> None:
    """Close every lease's client, isolating one failure from the rest.

    Each lease is already detached from the holder by the time it gets
    here, so a raise that stopped the loop would strand the remaining
    clients with nothing left holding a reference to retry them: their
    pools and sockets would be held for the life of the process. A close
    also runs on the way out of a successful request, where letting it
    raise would fail a request that had already worked.
    """
    for lease in leases:
        try:
            await lease.client.aclose()
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                SECURITY_TLS_CLIENT_CLOSE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )


class TrustFollowingClient:
    """Hands out an ``httpx.AsyncClient`` built for the current trust.

    Args:
        build: Builds a client from the trust argument to pass as
            ``verify=``. Every other setting (base URL, headers,
            timeouts) belongs to the owner and is closed over here, so a
            rebuild reproduces the same client against new trust.
    """

    __slots__ = ("_build", "_current", "_retired")

    def __init__(self, build: ClientBuilder) -> None:
        self._build = build
        self._current: _Lease | None = None
        self._retired: list[_Lease] = []

    def _lease(self) -> _Lease:
        """Return the lease for the current trust, rebuilding if it moved.

        Returns:
            The live :class:`_Lease`.
        """
        revision = trust_revision()
        current = self._current
        if current is not None and current.revision == revision:
            return current
        if current is not None:
            self._retired.append(current)
        fresh = _Lease(client=self._build(verify=httpx_verify()), revision=revision)
        self._current = fresh
        return fresh

    async def _reap(self) -> None:
        """Close every retired client nobody is riding on any more."""
        idle = [lease for lease in self._retired if lease.borrowers == 0]
        if not idle:
            return
        self._retired = [lease for lease in self._retired if lease.borrowers]
        await _close_all(idle)

    @asynccontextmanager
    async def borrow(self) -> AsyncIterator[httpx.AsyncClient]:
        """Yield the client for the current trust, holding it open.

        A trust change during the request does not disturb it: the swap
        installs a new client for the *next* borrower and leaves this one
        alive until this block exits.

        Yields:
            The live :class:`httpx.AsyncClient`.
        """
        lease = self._lease()
        lease.borrowers += 1
        try:
            yield lease.client
        finally:
            lease.borrowers -= 1
            await self._reap()

    async def aclose(self) -> None:
        """Close the current client and every retired one.

        Called on owner shutdown, where there is nothing left to protect:
        a borrower still in flight is being torn down with everything
        else.
        """
        leases = [*self._retired, *([self._current] if self._current else [])]
        self._retired = []
        self._current = None
        await _close_all(leases)


__all__ = ["ClientBuilder", "TrustFollowingClient"]
