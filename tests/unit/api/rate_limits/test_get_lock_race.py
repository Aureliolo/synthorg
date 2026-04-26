"""Concurrency test for InMemorySlidingWindowStore._get_lock (#1599 §4.4).

``_get_lock`` now always acquires the meta-lock so two simultaneous
callers cannot create two distinct ``asyncio.Lock`` instances for the
same key.
"""

import asyncio

import pytest

from synthorg.api.rate_limits.in_memory import InMemorySlidingWindowStore

pytestmark = pytest.mark.unit


async def test_get_lock_returns_same_instance_under_concurrency() -> None:
    """100 concurrent ``_get_lock("k")`` calls all return the same lock.

    Compares object identity (``is``) on the held references rather
    than ``id(lock)`` snapshots: ``id`` reuse after garbage collection
    is permitted by Python and would let a regression where each
    caller minted a fresh lock false-pass if the locks were
    reclaimed between the snapshot and the set check. We also pair
    every successful ``_get_lock`` with ``_release_lock_ref`` so the
    refcount returns to zero -- otherwise the test would leave
    ``_lock_refs["k"]`` artificially inflated and mask regressions
    in the GC's ref-count gate.
    """
    store = InMemorySlidingWindowStore()
    barrier = asyncio.Barrier(100)

    async def fetch_lock() -> asyncio.Lock:
        await barrier.wait()
        try:
            return await store._get_lock("k")
        finally:
            # Drop the borrowed ref under shield so a cancel cannot
            # leak the count.
            await asyncio.shield(store._release_lock_ref("k"))

    locks = await asyncio.gather(*[fetch_lock() for _ in range(100)])
    first = locks[0]
    for lock in locks[1:]:
        assert lock is first, (
            "Every concurrent caller must observe the same lock instance"
        )
    # Refcount must drop back to zero after every borrowed ref is
    # released; otherwise GC would refuse to evict the (now-stale)
    # lock and we'd leak.
    assert "k" not in store._lock_refs
