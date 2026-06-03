"""Unit tests for the shared per-conversation lock registry."""

import asyncio
from collections.abc import Callable

import pytest

from synthorg.meta.chief_of_staff.conversation_lock import ConversationLockRegistry

pytestmark = pytest.mark.unit

_MAX_SCHEDULING_YIELDS = 100


def _refcount(registry: ConversationLockRegistry, conversation_id: str) -> int:
    """Return the live refcount for a conversation, or 0 when evicted."""
    entry = registry._entries.get(conversation_id)
    return entry.refcount if entry is not None else 0


async def _wait_until(predicate: Callable[[], bool]) -> None:
    """Yield the event loop until ``predicate`` holds (bounded)."""
    for _ in range(_MAX_SCHEDULING_YIELDS):
        if predicate():
            return
        await asyncio.sleep(0)
    pytest.fail("condition not reached within the scheduling budget")


class TestConversationLockRegistry:
    async def test_serialises_same_conversation(self) -> None:
        # Two tasks holding the same conversation's lock must not
        # interleave their critical sections.
        registry = ConversationLockRegistry()
        order: list[str] = []

        async def worker(tag: str) -> None:
            async with registry.hold("conv-shared"):
                order.append(f"{tag}-start")
                await asyncio.sleep(0)
                order.append(f"{tag}-end")

        async with asyncio.TaskGroup() as tg:
            _ = tg.create_task(worker("a"))
            _ = tg.create_task(worker("b"))

        # Each worker's start/end are adjacent: no interleaving.
        assert order.index("a-end") == order.index("a-start") + 1
        assert order.index("b-end") == order.index("b-start") + 1

    async def test_distinct_conversations_run_concurrently(self) -> None:
        # Locks are per-id, so different conversations do not block
        # each other.
        registry = ConversationLockRegistry()
        gate = asyncio.Event()
        both_entered = asyncio.Barrier(2)

        async def worker(conversation_id: str) -> None:
            async with registry.hold(conversation_id):
                await both_entered.wait()
                gate.set()

        async with asyncio.TaskGroup() as tg:
            _ = tg.create_task(worker("conv-x"))
            _ = tg.create_task(worker("conv-y"))

        assert gate.is_set()

    async def test_evicts_lock_after_last_release(self) -> None:
        # The registry self-prunes: once the last holder releases, the
        # entry is gone, so the dict does not grow unbounded.
        registry = ConversationLockRegistry()
        async with registry.hold("conv-1"):
            assert "conv-1" in registry._entries
            assert _refcount(registry, "conv-1") == 1
        assert "conv-1" not in registry._entries

    async def test_queued_waiter_prevents_eviction_then_evicts(self) -> None:
        # A queued waiter increments the refcount before it blocks on the
        # lock, so eviction cannot orphan it; the entry survives while a
        # waiter is queued and is evicted only after both finish.
        registry = ConversationLockRegistry()
        order: list[str] = []
        first_holding = asyncio.Event()
        let_first_go = asyncio.Event()

        async def first() -> None:
            async with registry.hold("c"):
                order.append("first-in")
                first_holding.set()
                await let_first_go.wait()
                order.append("first-out")

        async def second() -> None:
            await first_holding.wait()
            async with registry.hold("c"):
                order.append("second-in")
                order.append("second-out")

        async with asyncio.TaskGroup() as tg:
            _ = tg.create_task(first())
            _ = tg.create_task(second())
            await first_holding.wait()
            # The second task increments to 2 as soon as it is scheduled
            # past ``first_holding.wait()``, then blocks on the lock.
            await _wait_until(lambda: _refcount(registry, "c") == 2)
            let_first_go.set()

        assert order == ["first-in", "first-out", "second-in", "second-out"]
        assert "c" not in registry._entries

    async def test_cancelled_holder_releases_and_evicts(self) -> None:
        # Cancelling a task while it holds the lock must still decrement
        # the refcount and evict the entry. The ``finally`` decrement has
        # no ``await``, so it completes during the cancellation unwind
        # before the task finishes: no stranded, never-evicted entry.
        registry = ConversationLockRegistry()
        holding = asyncio.Event()

        async def holder() -> None:
            async with registry.hold("c"):
                holding.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(holder())
        await holding.wait()
        assert _refcount(registry, "c") == 1

        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        assert "c" not in registry._entries
