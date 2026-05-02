"""Thread-safety tests for WsTicketStore.

The store's mutating methods (``create``, ``validate_and_consume``,
``cleanup_expired``) are synchronous because they were written for
single-threaded asyncio.  Litestar routes async handlers on the loop,
but operators may also dispatch sync handlers via the threadpool.
A ``threading.Lock`` guards count-and-insert, pop-and-validate, and
bulk eviction so concurrent thread access cannot exceed the per-user
cap or double-consume a ticket.
"""

import contextlib
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from synthorg.api.auth.models import AuthenticatedUser, AuthMethod
from synthorg.api.auth.ticket_store import TicketLimitExceededError, WsTicketStore
from synthorg.api.guards import HumanRole


def _make_user(user_id: str = "user-1") -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=user_id,
        username="testadmin",
        role=HumanRole.CEO,
        auth_method=AuthMethod.WS_TICKET,
    )


@pytest.mark.unit
class TestWsTicketStoreThreadSafety:
    """Concurrent access from a thread pool must honor invariants."""

    def test_concurrent_create_honors_per_user_cap(self) -> None:
        """100 threads racing on create() for one user yield exactly cap accepts."""
        store = WsTicketStore(max_pending_per_user=5)
        user = _make_user()
        # Hold all worker threads until every future is submitted, then
        # release together. Without the gate, futures execute as the
        # pool fills, which under-stresses the lock by spreading the
        # contention across submission time. With the gate, all 100
        # threads hit ``store.create`` in the same instant.
        start_gate = threading.Event()

        def attempt() -> str | None:
            start_gate.wait()
            try:
                return store.create(user)
            except TicketLimitExceededError:
                return None

        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = [pool.submit(attempt) for _ in range(100)]
            start_gate.set()
            results = [f.result() for f in futures]

        successes = [r for r in results if r is not None]
        assert len(successes) == 5
        assert len(set(successes)) == 5

    def test_concurrent_create_distinct_users_independent(self) -> None:
        """Different users do not share the cap under concurrency."""
        store = WsTicketStore(max_pending_per_user=3)
        start_gate = threading.Event()

        def attempt(user_id: str) -> str | None:
            start_gate.wait()
            try:
                return store.create(_make_user(user_id=user_id))
            except TicketLimitExceededError:
                return None

        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = [pool.submit(attempt, f"user-{i % 4}") for i in range(40)]
            start_gate.set()
            results = [f.result() for f in futures]

        successes = [r for r in results if r is not None]
        assert len(successes) == 12
        assert len(set(successes)) == 12

    def test_concurrent_validate_and_consume_single_winner(self) -> None:
        """A ticket can be consumed by exactly one thread under concurrency."""
        store = WsTicketStore()
        user = _make_user()
        ticket = store.create(user)
        start_gate = threading.Event()

        def attempt() -> AuthenticatedUser | None:
            start_gate.wait()
            return store.validate_and_consume(ticket)

        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = [pool.submit(attempt) for _ in range(32)]
            start_gate.set()
            results = [f.result() for f in futures]

        accepted = [r for r in results if r is not None]
        assert len(accepted) == 1
        assert accepted[0].user_id == user.user_id

    def test_concurrent_create_and_cleanup_no_corruption(self) -> None:
        """Mixed create / cleanup_expired calls do not raise or corrupt state."""
        store = WsTicketStore(ttl_seconds=30.0, max_pending_per_user=5)
        start_gate = threading.Event()

        def task(i: int) -> None:
            start_gate.wait()
            if i % 3 == 0:
                store.cleanup_expired()
                return
            with contextlib.suppress(TicketLimitExceededError):
                store.create(_make_user(user_id=f"user-{i % 8}"))

        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = [pool.submit(task, i) for i in range(80)]
            start_gate.set()
            for f in futures:
                f.result()
        # If we reach here without RuntimeError ("dictionary changed size
        # during iteration") or KeyError, the lock did its job.
