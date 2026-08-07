"""Tests for the httpx client that follows the TLS trust snapshot.

A client fixes its TLS configuration when it is built, so following a
trust change means building a new one. What the tests here pin is the
other half: the client that gets displaced owns a connection pool, and
whether it is closed, and when, decides between a socket leak and a
request that dies mid-flight.
"""

import ssl
from collections.abc import Iterator
from typing import override

import httpx
import pytest

from synthorg.core.http_trust_client import TrustFollowingClient
from synthorg.core.tls_trust import TlsTrust, current_tls_trust, set_tls_trust

pytestmark = pytest.mark.unit


class _RecordingClient(httpx.AsyncClient):
    """An httpx client that records the trust it was built with."""

    def __init__(self, *, verify: ssl.SSLContext | bool) -> None:
        super().__init__()
        self.built_with = verify
        self.close_count = 0
        self.raise_on_close = False

    @override
    async def aclose(self) -> None:
        """Count the close so a leak is visible as a zero.

        Raises:
            ConnectionResetError: When the test armed a failing close.
        """
        self.close_count += 1
        if self.raise_on_close:
            msg = "transport went away before it could be closed"
            raise ConnectionResetError(msg)
        await super().aclose()


@pytest.fixture(autouse=True)
def _restore_trust() -> Iterator[None]:
    """Leave the process-wide snapshot exactly as this module found it."""
    previous = current_tls_trust()
    yield
    set_tls_trust(previous)


def _holder() -> tuple[TrustFollowingClient, list[_RecordingClient]]:
    """Build a holder plus the list of clients it ends up building.

    Returns:
        The holder and the (initially empty) list of built clients.
    """
    built: list[_RecordingClient] = []

    def build(*, verify: ssl.SSLContext | bool) -> httpx.AsyncClient:
        client = _RecordingClient(verify=verify)
        built.append(client)
        return client

    return TrustFollowingClient(build), built


class TestReuse:
    async def test_a_settled_trust_hands_back_the_same_client(self) -> None:
        holder, built = _holder()

        async with holder.borrow() as first, holder.borrow() as second:
            assert first is second

        assert len(built) == 1
        await holder.aclose()


class TestRebuild:
    async def test_a_trust_change_builds_against_the_new_trust(self) -> None:
        holder, built = _holder()

        async with holder.borrow():
            pass
        set_tls_trust(TlsTrust(verify=False))
        async with holder.borrow():
            pass

        assert len(built) == 2
        assert built[1].built_with is False

    async def test_the_displaced_client_is_closed(self) -> None:
        """A dropped client leaks its connection pool and its sockets."""
        holder, built = _holder()

        async with holder.borrow():
            pass
        set_tls_trust(TlsTrust(verify=False))
        async with holder.borrow():
            pass

        assert built[0].close_count == 1

    async def test_a_borrowed_client_outlives_the_trust_change(self) -> None:
        """Closing under an in-flight request would fail that request."""
        holder, built = _holder()

        async with holder.borrow() as borrowed:
            set_tls_trust(TlsTrust(verify=False))
            async with holder.borrow() as replacement:
                assert replacement is not borrowed
            assert built[0].close_count == 0

        assert built[0].close_count == 1


class TestClose:
    async def test_closing_the_holder_closes_what_it_built(self) -> None:
        holder, built = _holder()

        async with holder.borrow():
            pass
        await holder.aclose()

        assert built[0].close_count == 1

    async def test_closing_twice_does_not_reclose(self) -> None:
        """Owners close on both ``__aexit__`` and explicit teardown."""
        holder, built = _holder()

        async with holder.borrow():
            pass
        await holder.aclose()
        await holder.aclose()

        assert built[0].close_count == 1

    async def test_an_unused_holder_closes_cleanly(self) -> None:
        holder, built = _holder()

        await holder.aclose()

        assert built == []

    async def test_one_failing_close_does_not_strand_the_others(self) -> None:
        """The holder has already let go, so a stopped loop leaks for good."""
        holder, built = _holder()

        async with holder.borrow():
            pass
        set_tls_trust(TlsTrust(verify=False))
        async with holder.borrow():
            pass
        built[0].raise_on_close = True

        await holder.aclose()

        assert built[1].close_count == 1

    async def test_a_failing_reap_does_not_fail_the_request(self) -> None:
        """Closing runs on the way out of a request that already worked."""
        holder, built = _holder()

        async with holder.borrow():
            pass
        built[0].raise_on_close = True
        set_tls_trust(TlsTrust(verify=False))

        async with holder.borrow() as client:
            assert client is built[1]
