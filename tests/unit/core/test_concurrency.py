"""Tests for the shared :class:`RefcountedLockMap` concurrency primitive."""

import asyncio

import pytest

from synthorg.core.concurrency import RefcountedLockMap


@pytest.mark.unit
class TestRefcountedLockMap:
    async def test_held_key_present_then_evicted(self) -> None:
        locks: RefcountedLockMap[str] = RefcountedLockMap()
        assert len(locks) == 0
        async with locks.acquire("k"):
            assert len(locks) == 1
        assert len(locks) == 0

    async def test_churn_leaves_map_empty(self) -> None:
        locks: RefcountedLockMap[str] = RefcountedLockMap()
        for i in range(50):
            async with locks.acquire(f"key-{i}"):
                pass
        assert len(locks) == 0

    async def test_same_key_serialises(self) -> None:
        locks: RefcountedLockMap[str] = RefcountedLockMap()
        events: list[str] = []
        first_holding = asyncio.Event()
        release_first = asyncio.Event()

        async def first() -> None:
            async with locks.acquire("k"):
                events.append("first-in")
                first_holding.set()
                await release_first.wait()
                events.append("first-out")

        async def second() -> None:
            await first_holding.wait()
            async with locks.acquire("k"):
                events.append("second-in")

        async with asyncio.TaskGroup() as tg:
            _ = tg.create_task(first())
            _ = tg.create_task(second())
            await first_holding.wait()
            release_first.set()

        # Interleaving would have placed "second-in" before "first-out".
        assert events == ["first-in", "first-out", "second-in"]
        assert len(locks) == 0

    async def test_concurrent_same_key_mutually_exclusive(self) -> None:
        locks: RefcountedLockMap[str] = RefcountedLockMap()
        inside = 0
        max_inside = 0

        async def worker() -> None:
            nonlocal inside, max_inside
            async with locks.acquire("shared"):
                inside += 1
                max_inside = max(max_inside, inside)
                await asyncio.sleep(0)
                inside -= 1

        async with asyncio.TaskGroup() as tg:
            for _ in range(20):
                _ = tg.create_task(worker())

        # The setdefault identity-race bug would let two coroutines hold
        # distinct locks for the same key and run concurrently (max > 1).
        assert max_inside == 1
        assert len(locks) == 0

    async def test_distinct_keys_run_concurrently(self) -> None:
        locks: RefcountedLockMap[str] = RefcountedLockMap()
        both_in = asyncio.Event()
        count = 0

        async def worker(key: str) -> None:
            nonlocal count
            async with locks.acquire(key):
                count += 1
                if count == 2:
                    both_in.set()
                await asyncio.wait_for(both_in.wait(), timeout=2.0)

        async with asyncio.TaskGroup() as tg:
            _ = tg.create_task(worker("a"))
            _ = tg.create_task(worker("b"))

        # If distinct keys did not run concurrently, both_in never sets and
        # wait_for would time out.
        assert both_in.is_set()
        assert len(locks) == 0
