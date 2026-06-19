"""Concurrency tests for WsTicketStore.

The store's mutating methods (``create``, ``validate_and_consume``,
``cleanup_expired``) are async and guard count-and-insert,
pop-and-validate, and bulk eviction with an ``asyncio.Lock``. These
tests fan out many coroutines on one event loop via ``asyncio.gather``
so concurrent access cannot exceed the per-user cap or double-consume
a ticket.
"""

import asyncio
import contextlib

import pytest

from synthorg.api.auth.ticket_store import TicketLimitExceededError, WsTicketStore
from synthorg.core.auth.models import AuthenticatedUser, AuthMethod
from synthorg.core.auth.roles import HumanRole


def _make_user(user_id: str = "user-1") -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=user_id,
        username="testadmin",
        role=HumanRole.CEO,
        auth_method=AuthMethod.WS_TICKET,
    )


@pytest.mark.unit
class TestWsTicketStoreConcurrency:
    """Concurrent coroutine access must honor invariants."""

    async def test_concurrent_create_honors_per_user_cap(self) -> None:
        """100 coroutines racing on create() for one user yield exactly cap accepts."""
        store = WsTicketStore(max_pending_per_user=5)
        user = _make_user()

        async def attempt() -> str | None:
            try:
                return await store.create(user)
            except TicketLimitExceededError:
                return None

        results = await asyncio.gather(*(attempt() for _ in range(100)))

        successes = [r for r in results if r is not None]
        assert len(successes) == 5
        assert len(set(successes)) == 5

    async def test_concurrent_create_distinct_users_independent(self) -> None:
        """Different users do not share the cap under concurrency."""
        store = WsTicketStore(max_pending_per_user=3)

        async def attempt(user_id: str) -> str | None:
            try:
                return await store.create(_make_user(user_id=user_id))
            except TicketLimitExceededError:
                return None

        results = await asyncio.gather(*(attempt(f"user-{i % 4}") for i in range(40)))

        successes = [r for r in results if r is not None]
        assert len(successes) == 12
        assert len(set(successes)) == 12

    async def test_concurrent_validate_and_consume_single_winner(self) -> None:
        """A ticket can be consumed by exactly one coroutine under concurrency."""
        store = WsTicketStore()
        user = _make_user()
        ticket = await store.create(user)

        results = await asyncio.gather(
            *(store.validate_and_consume(ticket) for _ in range(32))
        )

        accepted = [r for r in results if r is not None]
        assert len(accepted) == 1
        assert accepted[0].user_id == user.user_id

    async def test_concurrent_create_and_cleanup_no_corruption(self) -> None:
        """Mixed create / cleanup_expired calls do not raise or corrupt state."""
        store = WsTicketStore(ttl_seconds=30.0, max_pending_per_user=5)

        async def task(i: int) -> None:
            if i % 3 == 0:
                await store.cleanup_expired()
                return
            with contextlib.suppress(TicketLimitExceededError):
                await store.create(_make_user(user_id=f"user-{i % 8}"))

        await asyncio.gather(*(task(i) for i in range(80)))
        # If we reach here without RuntimeError ("dictionary changed size
        # during iteration") or KeyError, the lock did its job.
