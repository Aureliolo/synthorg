"""Tests for ArchivalStore protocol compliance."""

from datetime import UTC, datetime

import pytest

from synthorg.core.enums import MemoryCategory
from synthorg.memory.consolidation.archival import ArchivalStore
from synthorg.memory.consolidation.models import ArchivalEntry

pytestmark = pytest.mark.timeout(30)

_NOW = datetime.now(UTC)


class _MockArchivalStore:
    """Mock implementation of ArchivalStore for protocol tests."""

    def __init__(self) -> None:
        self._entries: dict[str, ArchivalEntry] = {}
        self._next_id = 0

    async def archive(self, entry: ArchivalEntry) -> str:
        self._next_id += 1
        archive_id = f"archive-{self._next_id}"
        self._entries[archive_id] = entry
        return archive_id

    async def search(
        self,
        agent_id: str,
        query: object,
    ) -> tuple[ArchivalEntry, ...]:
        return tuple(e for e in self._entries.values() if e.agent_id == agent_id)

    async def restore(
        self,
        agent_id: str,
        entry_id: str,
    ) -> ArchivalEntry | None:
        entry = self._entries.get(entry_id)
        if entry is not None and entry.agent_id == agent_id:
            return entry
        return None

    async def count(self, agent_id: str) -> int:
        return sum(1 for e in self._entries.values() if e.agent_id == agent_id)


@pytest.mark.unit
class TestArchivalStoreProtocol:
    """ArchivalStore is runtime_checkable."""

    def test_mock_is_instance(self) -> None:
        store = _MockArchivalStore()
        assert isinstance(store, ArchivalStore)


@pytest.mark.unit
class TestMockArchivalStoreRoundTrip:
    """Archive/search/restore round-trip."""

    async def test_archive_and_search(self) -> None:
        store = _MockArchivalStore()
        entry = ArchivalEntry(
            original_id="mem-1",
            agent_id="agent-1",
            content="Archived memory",
            category=MemoryCategory.EPISODIC,
            created_at=_NOW,
            archived_at=_NOW,
        )
        archive_id = await store.archive(entry)
        assert archive_id == "archive-1"

        results = await store.search("agent-1", None)
        assert len(results) == 1
        assert results[0].original_id == "mem-1"

    async def test_restore(self) -> None:
        store = _MockArchivalStore()
        entry = ArchivalEntry(
            original_id="mem-1",
            agent_id="agent-1",
            content="Test",
            category=MemoryCategory.WORKING,
            created_at=_NOW,
            archived_at=_NOW,
        )
        archive_id = await store.archive(entry)
        restored = await store.restore("agent-1", archive_id)
        assert restored is not None
        assert restored.original_id == "mem-1"

    async def test_restore_nonexistent(self) -> None:
        store = _MockArchivalStore()
        assert await store.restore("agent-1", "nonexistent") is None

    async def test_count(self) -> None:
        store = _MockArchivalStore()
        entry = ArchivalEntry(
            original_id="mem-1",
            agent_id="agent-1",
            content="Test",
            category=MemoryCategory.WORKING,
            created_at=_NOW,
            archived_at=_NOW,
        )
        await store.archive(entry)
        assert await store.count("agent-1") == 1
        assert await store.count("agent-2") == 0
