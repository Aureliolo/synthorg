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
    """100 concurrent ``_get_lock("k")`` calls all return the same lock."""
    store = InMemorySlidingWindowStore()
    barrier = asyncio.Barrier(100)

    async def fetch_lock() -> int:
        await barrier.wait()
        lock = await store._get_lock("k")
        return id(lock)

    ids = await asyncio.gather(*[fetch_lock() for _ in range(100)])
    assert len(set(ids)) == 1, (
        "Every concurrent caller must observe the same lock instance"
    )
