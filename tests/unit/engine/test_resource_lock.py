"""Tests for resource locking."""

import asyncio

import pytest

from synthorg.engine.resource_lock import InMemoryResourceLock, ResourceLock


@pytest.mark.unit
class TestResourceLockProtocol:
    """ResourceLock protocol compliance."""

    def test_in_memory_is_resource_lock(self) -> None:
        lock = InMemoryResourceLock()
        assert isinstance(lock, ResourceLock)


@pytest.mark.unit
class TestInMemoryResourceLock:
    """InMemoryResourceLock implementation."""

    async def test_acquire_uncontested(self) -> None:
        lock = InMemoryResourceLock()
        result = await lock.acquire("file.py", "agent-1")
        assert result is True

    async def test_acquire_same_holder_idempotent(self) -> None:
        lock = InMemoryResourceLock()
        await lock.acquire("file.py", "agent-1")
        result = await lock.acquire("file.py", "agent-1")
        assert result is True

    async def test_acquire_different_holder_fails(self) -> None:
        lock = InMemoryResourceLock()
        await lock.acquire("file.py", "agent-1")
        result = await lock.acquire("file.py", "agent-2")
        assert result is False

    async def test_release_allows_reacquire(self) -> None:
        lock = InMemoryResourceLock()
        await lock.acquire("file.py", "agent-1")
        await lock.release("file.py", "agent-1")
        result = await lock.acquire("file.py", "agent-2")
        assert result is True

    async def test_release_wrong_holder_noop(self) -> None:
        lock = InMemoryResourceLock()
        await lock.acquire("file.py", "agent-1")
        await lock.release("file.py", "agent-2")
        assert lock.is_locked("file.py")
        assert lock.holder_of("file.py") == "agent-1"

    async def test_release_unlocked_noop(self) -> None:
        lock = InMemoryResourceLock()
        await lock.release("file.py", "agent-1")
        assert not lock.is_locked("file.py")

    async def test_release_all(self) -> None:
        lock = InMemoryResourceLock()
        await lock.acquire("a.py", "agent-1")
        await lock.acquire("b.py", "agent-1")
        await lock.acquire("c.py", "agent-2")

        released = await lock.release_all("agent-1")

        assert released == 2
        assert not lock.is_locked("a.py")
        assert not lock.is_locked("b.py")
        assert lock.is_locked("c.py")

    async def test_release_all_none_held(self) -> None:
        lock = InMemoryResourceLock()
        released = await lock.release_all("agent-1")
        assert released == 0

    async def test_is_locked(self) -> None:
        lock = InMemoryResourceLock()
        assert not lock.is_locked("file.py")
        await lock.acquire("file.py", "agent-1")
        assert lock.is_locked("file.py")

    async def test_holder_of(self) -> None:
        lock = InMemoryResourceLock()
        assert lock.holder_of("file.py") is None
        await lock.acquire("file.py", "agent-1")
        assert lock.holder_of("file.py") == "agent-1"

    async def test_concurrent_acquire_only_one_wins(self) -> None:
        lock = InMemoryResourceLock()
        results: list[bool] = []

        async def try_acquire(holder: str) -> None:
            result = await lock.acquire("file.py", holder)
            results.append(result)

        async with asyncio.TaskGroup() as tg:
            for i in range(5):
                tg.create_task(try_acquire(f"agent-{i}"))

        assert results.count(True) == 1
        assert results.count(False) == 4

    async def test_multiple_resources_independent(self) -> None:
        lock = InMemoryResourceLock()
        r1 = await lock.acquire("a.py", "agent-1")
        r2 = await lock.acquire("b.py", "agent-2")
        assert r1 is True
        assert r2 is True
        assert lock.holder_of("a.py") == "agent-1"
        assert lock.holder_of("b.py") == "agent-2"
