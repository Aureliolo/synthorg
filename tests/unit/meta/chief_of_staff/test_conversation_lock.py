"""Unit tests for the shared per-conversation lock registry."""

import asyncio

import pytest

from synthorg.meta.chief_of_staff.conversation_lock import ConversationLockRegistry

pytestmark = pytest.mark.unit


class TestConversationLockRegistry:
    async def test_same_id_returns_same_lock(self) -> None:
        registry = ConversationLockRegistry()
        first = await registry.acquire_for("conv-1")
        second = await registry.acquire_for("conv-1")
        assert first is second

    async def test_distinct_ids_get_distinct_locks(self) -> None:
        registry = ConversationLockRegistry()
        lock_a = await registry.acquire_for("conv-a")
        lock_b = await registry.acquire_for("conv-b")
        assert lock_a is not lock_b

    async def test_serialises_same_conversation(self) -> None:
        # Two tasks holding the same conversation's lock must not
        # interleave their critical sections.
        registry = ConversationLockRegistry()
        order: list[str] = []

        async def worker(tag: str) -> None:
            async with await registry.acquire_for("conv-shared"):
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
            async with await registry.acquire_for(conversation_id):
                await both_entered.wait()
                gate.set()

        async with asyncio.TaskGroup() as tg:
            _ = tg.create_task(worker("conv-x"))
            _ = tg.create_task(worker("conv-y"))

        assert gate.is_set()
