"""Unit tests for :class:`InMemorySlidingWindowStore`."""

import asyncio

import pytest

from synthorg.api.rate_limits.in_memory import InMemorySlidingWindowStore
from tests._shared.fake_clock import FakeClock

pytestmark = pytest.mark.unit


class TestSlidingWindow:
    """Allow/reject timing for the in-memory store."""

    async def test_allows_up_to_max_requests(self) -> None:
        store = InMemorySlidingWindowStore()
        for _ in range(5):
            outcome = await store.acquire(
                "op:user:1",
                max_requests=5,
                window_seconds=60,
            )
            assert outcome.allowed is True
            assert outcome.retry_after_seconds is None

    async def test_rejects_after_max_requests(self) -> None:
        store = InMemorySlidingWindowStore()
        for _ in range(3):
            await store.acquire("k", max_requests=3, window_seconds=60)
        outcome = await store.acquire("k", max_requests=3, window_seconds=60)
        assert outcome.allowed is False
        assert outcome.retry_after_seconds is not None
        assert outcome.retry_after_seconds > 0.0
        assert outcome.retry_after_seconds <= 60.0

    async def test_eviction_after_window_expires(self) -> None:
        """Old timestamps are dropped; the bucket re-opens."""
        clock = FakeClock()
        store = InMemorySlidingWindowStore(clock=clock)
        for _ in range(2):
            outcome = await store.acquire(
                "k",
                max_requests=2,
                window_seconds=10,
            )
            assert outcome.allowed is True
        rejected = await store.acquire("k", max_requests=2, window_seconds=10)
        assert rejected.allowed is False

        # Advance past the window; the bucket should be empty again.
        clock.advance(11.0)
        allowed = await store.acquire("k", max_requests=2, window_seconds=10)
        assert allowed.allowed is True

    async def test_isolated_buckets_per_key(self) -> None:
        store = InMemorySlidingWindowStore()
        for _ in range(2):
            await store.acquire("user:a", max_requests=2, window_seconds=60)
        # "a" exhausted, "b" unaffected.
        rejected = await store.acquire(
            "user:a",
            max_requests=2,
            window_seconds=60,
        )
        allowed = await store.acquire(
            "user:b",
            max_requests=2,
            window_seconds=60,
        )
        assert rejected.allowed is False
        assert allowed.allowed is True

    async def test_concurrent_acquires_respect_budget(self) -> None:
        """50 concurrent calls to a 10/window bucket let exactly 10 through."""
        store = InMemorySlidingWindowStore()

        async def _try() -> bool:
            outcome = await store.acquire(
                "concurrent",
                max_requests=10,
                window_seconds=60,
            )
            return outcome.allowed

        results = await asyncio.gather(*[_try() for _ in range(50)])
        assert sum(results) == 10

    async def test_retry_after_reflects_oldest_timestamp(self) -> None:
        """Rejection returns the wait until the oldest request ages out."""
        clock = FakeClock()
        store = InMemorySlidingWindowStore(clock=clock)
        await store.acquire("k", max_requests=1, window_seconds=30)
        clock.advance(5.0)
        outcome = await store.acquire("k", max_requests=1, window_seconds=30)
        assert outcome.allowed is False
        # Oldest at t=0, window=30 -> expires at t=30. Current is
        # t=5, so retry ~= 25 seconds.
        assert outcome.retry_after_seconds is not None
        assert 24.0 < outcome.retry_after_seconds < 26.0

    async def test_invalid_arguments(self) -> None:
        store = InMemorySlidingWindowStore()
        with pytest.raises(ValueError, match="max_requests"):
            await store.acquire("k", max_requests=0, window_seconds=60)
        with pytest.raises(ValueError, match="window_seconds"):
            await store.acquire("k", max_requests=10, window_seconds=0)

    async def test_close_clears_buckets(self) -> None:
        store = InMemorySlidingWindowStore()
        await store.acquire("k", max_requests=5, window_seconds=60)
        await store.close()
        # After close, a fresh acquire starts from zero.
        outcome = await store.acquire("k", max_requests=1, window_seconds=60)
        assert outcome.allowed is True
