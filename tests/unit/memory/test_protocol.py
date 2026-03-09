"""Tests for memory protocol compliance."""

from datetime import UTC, datetime
from uuid import uuid4

import pytest

from ai_company.core.enums import MemoryCategory
from ai_company.core.types import NotBlankStr
from ai_company.memory.models import (
    MemoryEntry,
    MemoryMetadata,
    MemoryQuery,
    MemoryStoreRequest,
)
from ai_company.memory.protocol import MemoryBackend

pytestmark = pytest.mark.timeout(30)


class _FakeMemoryBackend:
    """Minimal in-memory backend for protocol compliance testing."""

    def __init__(self) -> None:
        self._connected = False
        self._store: dict[str, dict[str, MemoryEntry]] = {}

    async def connect(self) -> None:
        self._connected = True

    async def disconnect(self) -> None:
        self._connected = False

    async def health_check(self) -> bool:
        return self._connected

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def backend_name(self) -> NotBlankStr:
        return NotBlankStr("fake")

    async def store(self, agent_id: str, request: MemoryStoreRequest) -> str:
        memory_id = str(uuid4())
        agent_store = self._store.setdefault(agent_id, {})
        entry = MemoryEntry(
            id=memory_id,
            agent_id=agent_id,
            category=request.category,
            content=request.content,
            metadata=request.metadata,
            created_at=datetime.now(tz=UTC),
            expires_at=request.expires_at,
        )
        agent_store[memory_id] = entry
        return memory_id

    async def retrieve(
        self, agent_id: str, query: MemoryQuery
    ) -> tuple[MemoryEntry, ...]:
        agent_store = self._store.get(agent_id, {})
        results = list(agent_store.values())
        if query.categories is not None:
            results = [e for e in results if e.category in query.categories]
        return tuple(results[: query.limit])

    async def get(self, agent_id: str, memory_id: str) -> MemoryEntry | None:
        return self._store.get(agent_id, {}).get(memory_id)

    async def delete(self, agent_id: str, memory_id: str) -> bool:
        agent_store = self._store.get(agent_id, {})
        if memory_id in agent_store:
            del agent_store[memory_id]
            return True
        return False

    async def count(
        self,
        agent_id: str,
        *,
        category: MemoryCategory | None = None,
    ) -> int:
        agent_store = self._store.get(agent_id, {})
        if category is None:
            return len(agent_store)
        return sum(1 for e in agent_store.values() if e.category is category)


class _IncompleteBackend:
    """Missing required methods — should fail isinstance check."""

    async def connect(self) -> None: ...

    @property
    def is_connected(self) -> bool:
        return False


@pytest.mark.unit
class TestProtocolCompliance:
    def test_fake_backend_is_memory_backend(self) -> None:
        assert isinstance(_FakeMemoryBackend(), MemoryBackend)

    def test_incomplete_class_fails_isinstance(self) -> None:
        assert not isinstance(_IncompleteBackend(), MemoryBackend)

    def test_plain_object_fails_isinstance(self) -> None:
        assert not isinstance(object(), MemoryBackend)


@pytest.mark.unit
class TestFakeBackendBehaviour:
    async def test_store_and_retrieve(self) -> None:
        backend = _FakeMemoryBackend()
        await backend.connect()

        memory_id = await backend.store(
            "agent-a",
            MemoryStoreRequest(
                category=MemoryCategory.EPISODIC,
                content="test event",
            ),
        )
        assert isinstance(memory_id, str)

        results = await backend.retrieve("agent-a", MemoryQuery())
        assert len(results) == 1
        assert results[0].content == "test event"

    async def test_per_agent_isolation(self) -> None:
        backend = _FakeMemoryBackend()
        await backend.connect()

        await backend.store(
            "agent-a",
            MemoryStoreRequest(
                category=MemoryCategory.SEMANTIC,
                content="agent-a fact",
            ),
        )
        await backend.store(
            "agent-b",
            MemoryStoreRequest(
                category=MemoryCategory.SEMANTIC,
                content="agent-b fact",
            ),
        )

        results_a = await backend.retrieve("agent-a", MemoryQuery())
        results_b = await backend.retrieve("agent-b", MemoryQuery())

        assert len(results_a) == 1
        assert results_a[0].content == "agent-a fact"
        assert len(results_b) == 1
        assert results_b[0].content == "agent-b fact"

    async def test_get_by_id(self) -> None:
        backend = _FakeMemoryBackend()
        await backend.connect()

        memory_id = await backend.store(
            "agent-a",
            MemoryStoreRequest(
                category=MemoryCategory.WORKING,
                content="temp context",
            ),
        )
        entry = await backend.get("agent-a", memory_id)
        assert entry is not None
        assert entry.content == "temp context"

    async def test_get_missing_returns_none(self) -> None:
        backend = _FakeMemoryBackend()
        assert await backend.get("agent-a", "nonexistent") is None

    async def test_delete(self) -> None:
        backend = _FakeMemoryBackend()
        await backend.connect()

        memory_id = await backend.store(
            "agent-a",
            MemoryStoreRequest(
                category=MemoryCategory.PROCEDURAL,
                content="step 1",
            ),
        )
        assert await backend.delete("agent-a", memory_id) is True
        assert await backend.get("agent-a", memory_id) is None

    async def test_delete_missing_returns_false(self) -> None:
        backend = _FakeMemoryBackend()
        assert await backend.delete("agent-a", "nonexistent") is False

    async def test_count(self) -> None:
        backend = _FakeMemoryBackend()
        await backend.connect()

        await backend.store(
            "agent-a",
            MemoryStoreRequest(
                category=MemoryCategory.EPISODIC,
                content="event 1",
            ),
        )
        await backend.store(
            "agent-a",
            MemoryStoreRequest(
                category=MemoryCategory.SEMANTIC,
                content="fact 1",
            ),
        )

        assert await backend.count("agent-a") == 2
        assert await backend.count("agent-a", category=MemoryCategory.EPISODIC) == 1
        assert await backend.count("agent-b") == 0

    async def test_not_connected_initially(self) -> None:
        backend = _FakeMemoryBackend()
        assert backend.is_connected is False

    async def test_connected_after_connect(self) -> None:
        backend = _FakeMemoryBackend()
        await backend.connect()
        assert backend.is_connected is True
        assert await backend.health_check() is True

    async def test_disconnected_after_disconnect(self) -> None:
        backend = _FakeMemoryBackend()
        await backend.connect()
        await backend.disconnect()
        assert backend.is_connected is False

    async def test_retrieve_with_category_filter(self) -> None:
        backend = _FakeMemoryBackend()
        await backend.connect()

        await backend.store(
            "agent-a",
            MemoryStoreRequest(
                category=MemoryCategory.EPISODIC,
                content="event",
            ),
        )
        await backend.store(
            "agent-a",
            MemoryStoreRequest(
                category=MemoryCategory.SEMANTIC,
                content="fact",
            ),
        )

        results = await backend.retrieve(
            "agent-a",
            MemoryQuery(categories=frozenset({MemoryCategory.SEMANTIC})),
        )
        assert len(results) == 1
        assert results[0].category is MemoryCategory.SEMANTIC

    async def test_store_with_metadata(self) -> None:
        backend = _FakeMemoryBackend()
        await backend.connect()

        memory_id = await backend.store(
            "agent-a",
            MemoryStoreRequest(
                category=MemoryCategory.EPISODIC,
                content="detailed event",
                metadata=MemoryMetadata(
                    source="task-789",
                    confidence=0.85,
                    tags=("important",),
                ),
            ),
        )
        entry = await backend.get("agent-a", memory_id)
        assert entry is not None
        assert entry.metadata.source == "task-789"
        assert entry.metadata.confidence == 0.85
        assert entry.metadata.tags == ("important",)
