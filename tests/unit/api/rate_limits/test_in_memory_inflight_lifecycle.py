"""Lifecycle teardown coverage for :class:`InMemoryInflightStore`.

Defensive coverage for the rate-limit inflight tracker: every state
collection drops on ``close()`` and double-close is idempotent. A
worker pool that resets the limiter between scenarios must not leave
counters or locks behind.
"""

import pytest

from synthorg.api.rate_limits.in_memory_inflight import InMemoryInflightStore

pytestmark = pytest.mark.unit


async def test_close_clears_all_collections() -> None:
    store = InMemoryInflightStore()
    async with store.acquire(key="agent-1", max_inflight=2):
        pass
    assert "agent-1" in store._counters
    await store.close()
    assert not store._counters
    assert not store._locks
    assert not store._lock_refs


async def test_close_is_idempotent() -> None:
    store = InMemoryInflightStore()
    await store.close()
    # Second close must not raise.
    await store.close()
    assert not store._counters
