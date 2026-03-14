"""Tests for shared knowledge store protocol compliance."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from synthorg.core.enums import MemoryCategory
from synthorg.memory.models import (
    MemoryEntry,
    MemoryQuery,
    MemoryStoreRequest,
)
from synthorg.memory.shared import SharedKnowledgeStore

pytestmark = pytest.mark.timeout(30)


class _FakeSharedKnowledgeStore:
    """Minimal implementation for protocol compliance testing."""

    def __init__(self) -> None:
        self._shared: dict[str, MemoryEntry] = {}

    async def publish(self, agent_id: str, request: MemoryStoreRequest) -> str:
        memory_id = str(uuid4())
        self._shared[memory_id] = MemoryEntry(
            id=memory_id,
            agent_id=agent_id,
            category=request.category,
            content=request.content,
            metadata=request.metadata,
            created_at=datetime.now(tz=UTC),
        )
        return memory_id

    async def search_shared(
        self,
        query: MemoryQuery,
        *,
        exclude_agent: str | None = None,
    ) -> tuple[MemoryEntry, ...]:
        results = list(self._shared.values())
        if exclude_agent is not None:
            results = [e for e in results if e.agent_id != exclude_agent]
        return tuple(results[: query.limit])

    async def retract(self, agent_id: str, memory_id: str) -> bool:
        entry = self._shared.get(memory_id)
        if entry is not None and entry.agent_id == agent_id:
            del self._shared[memory_id]
            return True
        return False


class _IncompleteSharedStore:
    """Missing required methods — should fail isinstance check."""

    async def publish(self, agent_id: str, request: MemoryStoreRequest) -> str:
        return "id"


@pytest.mark.unit
class TestSharedProtocolCompliance:
    def test_fake_is_shared_knowledge_store(self) -> None:
        assert isinstance(_FakeSharedKnowledgeStore(), SharedKnowledgeStore)

    def test_incomplete_class_fails_isinstance(self) -> None:
        assert not isinstance(_IncompleteSharedStore(), SharedKnowledgeStore)

    def test_plain_object_fails_isinstance(self) -> None:
        assert not isinstance(object(), SharedKnowledgeStore)


@pytest.mark.unit
class TestFakeSharedBehaviour:
    async def test_publish_and_search(self) -> None:
        store = _FakeSharedKnowledgeStore()
        memory_id = await store.publish(
            "agent-a",
            MemoryStoreRequest(
                category=MemoryCategory.SEMANTIC,
                content="shared fact",
            ),
        )
        assert isinstance(memory_id, str)

        results = await store.search_shared(MemoryQuery())
        assert len(results) == 1
        assert results[0].content == "shared fact"

    async def test_search_with_exclude(self) -> None:
        store = _FakeSharedKnowledgeStore()
        await store.publish(
            "agent-a",
            MemoryStoreRequest(
                category=MemoryCategory.SEMANTIC,
                content="from a",
            ),
        )
        await store.publish(
            "agent-b",
            MemoryStoreRequest(
                category=MemoryCategory.SEMANTIC,
                content="from b",
            ),
        )

        results = await store.search_shared(MemoryQuery(), exclude_agent="agent-a")
        assert len(results) == 1
        assert results[0].agent_id == "agent-b"

    async def test_retract(self) -> None:
        store = _FakeSharedKnowledgeStore()
        memory_id = await store.publish(
            "agent-a",
            MemoryStoreRequest(
                category=MemoryCategory.SEMANTIC,
                content="retractable",
            ),
        )
        assert await store.retract("agent-a", memory_id) is True
        results = await store.search_shared(MemoryQuery())
        assert len(results) == 0

    async def test_retract_wrong_agent_returns_false(self) -> None:
        store = _FakeSharedKnowledgeStore()
        memory_id = await store.publish(
            "agent-a",
            MemoryStoreRequest(
                category=MemoryCategory.SEMANTIC,
                content="owned by a",
            ),
        )
        assert await store.retract("agent-b", memory_id) is False

    async def test_retract_missing_returns_false(self) -> None:
        store = _FakeSharedKnowledgeStore()
        assert await store.retract("agent-a", "nonexistent") is False
