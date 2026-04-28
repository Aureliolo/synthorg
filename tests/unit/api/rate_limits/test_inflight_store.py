"""Unit tests for the in-memory inflight store."""

import asyncio

import pytest

from synthorg.api.rate_limits.in_memory_inflight import InMemoryInflightStore
from synthorg.core.domain_errors import ConcurrencyLimitExceededError

pytestmark = pytest.mark.unit


class TestAcquireRelease:
    """Basic acquire/release: counter increments and decrements."""

    async def test_acquire_below_cap_succeeds(self) -> None:
        store = InMemoryInflightStore()
        try:
            async with store.acquire("op:user-1", max_inflight=2):
                assert store._counters["op:user-1"] == 1
            assert store._counters["op:user-1"] == 0
        finally:
            await store.close()

    async def test_sequential_acquires_at_cap_succeed(self) -> None:
        store = InMemoryInflightStore()
        try:
            for _ in range(5):
                async with store.acquire("op:user-1", max_inflight=1):
                    assert store._counters["op:user-1"] == 1
                assert store._counters["op:user-1"] == 0
        finally:
            await store.close()

    async def test_release_on_exception_decrements(self) -> None:
        store = InMemoryInflightStore()

        async def raise_inside_permit() -> None:
            async with store.acquire("op:user-1", max_inflight=1):
                assert store._counters["op:user-1"] == 1
                msg = "boom"
                raise RuntimeError(msg)

        try:
            with pytest.raises(RuntimeError, match="boom"):
                await raise_inside_permit()
            assert store._counters["op:user-1"] == 0
        finally:
            await store.close()


class TestConcurrencyDenial:
    """Over-limit requests raise ConcurrencyLimitExceededError."""

    async def test_concurrent_at_cap_raises(self) -> None:
        store = InMemoryInflightStore()
        gate = asyncio.Event()
        acquired = asyncio.Event()

        async def holder() -> None:
            async with store.acquire("op:user-1", max_inflight=1):
                # Signal the attempt coroutine only after the permit is
                # held -- using an ``Event`` instead of ``sleep(0)`` is
                # deterministic under any event-loop scheduling order.
                acquired.set()
                await gate.wait()

        async def attempt() -> ConcurrencyLimitExceededError:
            try:
                async with store.acquire("op:user-1", max_inflight=1):
                    msg = "Second acquire should have been denied"
                    raise AssertionError(msg)
            except ConcurrencyLimitExceededError as exc:
                return exc

        try:
            holder_task = asyncio.create_task(holder())
            await acquired.wait()
            err = await attempt()
            assert err.retry_after == 1
            assert err.status_code == 429
            assert err.error_code.value == 5002
            gate.set()
            await holder_task
        finally:
            await store.close()

    async def test_release_reopens_slot(self) -> None:
        store = InMemoryInflightStore()
        try:
            async with store.acquire("op:user-1", max_inflight=1):
                pass
            async with store.acquire("op:user-1", max_inflight=1):
                assert store._counters["op:user-1"] == 1
        finally:
            await store.close()

    async def test_distinct_keys_dont_share_cap(self) -> None:
        store = InMemoryInflightStore()
        try:
            async with (
                store.acquire("op:user-1", max_inflight=1),
                store.acquire("op:user-2", max_inflight=1),
            ):
                assert store._counters["op:user-1"] == 1
                assert store._counters["op:user-2"] == 1
        finally:
            await store.close()


class TestValidation:
    """Invalid max_inflight raises ValueError immediately."""

    async def test_zero_max_inflight_raises(self) -> None:
        store = InMemoryInflightStore()
        try:
            with pytest.raises(ValueError, match="max_inflight"):
                store.acquire("op:user-1", max_inflight=0)
        finally:
            await store.close()

    async def test_negative_max_inflight_raises(self) -> None:
        store = InMemoryInflightStore()
        try:
            with pytest.raises(ValueError, match="max_inflight"):
                store.acquire("op:user-1", max_inflight=-5)
        finally:
            await store.close()


class TestClose:
    """close() clears internal state."""

    async def test_close_clears_counters(self) -> None:
        store = InMemoryInflightStore()
        async with store.acquire("op:user-1", max_inflight=1):
            pass
        assert "op:user-1" in store._counters
        await store.close()
        assert not store._counters
        assert not store._locks


class TestNegativeRelease:
    """``_release`` on a counter already at zero clamps + warns."""

    async def test_release_without_acquire_clamps_to_zero(self) -> None:
        store = InMemoryInflightStore()
        try:
            # Simulate a release on a key that was never acquired -- the
            # code path the clamp is defensively guarding against.  The
            # counter must stay at zero, and a subsequent legitimate
            # acquire must still succeed (no lingering corruption).
            await store._release("op:phantom")
            assert store._counters.get("op:phantom", 0) == 0
            async with store.acquire("op:phantom", max_inflight=1):
                assert store._counters["op:phantom"] == 1
            assert store._counters["op:phantom"] == 0
        finally:
            await store.close()


class TestGcSweep:
    """``_gc_cold_buckets`` reclaims empty buckets + orphan locks."""

    async def test_empty_bucket_is_reaped(self) -> None:
        store = InMemoryInflightStore()
        try:
            async with store.acquire("op:cold", max_inflight=1):
                pass
            # Counter is back at zero; lock is unlocked.  Sweep now.
            assert store._counters.get("op:cold") == 0
            await store._gc_cold_buckets()
            assert "op:cold" not in store._counters
            assert "op:cold" not in store._locks
        finally:
            await store.close()

    async def test_active_bucket_is_not_reaped(self) -> None:
        store = InMemoryInflightStore()

        async def hold() -> None:
            async with store.acquire("op:hot", max_inflight=1):
                # While held, a sweep must not delete the counter; the
                # lock is locked, and the counter is > 0.
                await store._gc_cold_buckets()
                assert store._counters["op:hot"] == 1
                assert "op:hot" in store._locks

        try:
            await hold()
        finally:
            await store.close()

    async def test_orphan_lock_is_reaped(self) -> None:
        store = InMemoryInflightStore()
        try:
            # Create a lock entry without a matching counter (simulates
            # a cancelled acquire that created the lock lazily but never
            # materialised the counter).
            _ = await store._get_lock("op:orphan")
            assert "op:orphan" in store._locks
            assert "op:orphan" not in store._counters
            await store._gc_cold_buckets()
            # Orphan locks that are not currently held must be dropped.
            assert "op:orphan" not in store._locks
        finally:
            await store.close()
