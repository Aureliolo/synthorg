"""Tests for FullSnapshotStrategy."""

from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from synthorg.core.enums import MemoryCategory, SeniorityLevel
from synthorg.core.types import NotBlankStr
from synthorg.hr.errors import MemoryArchivalError
from synthorg.hr.full_snapshot_strategy import FullSnapshotStrategy
from synthorg.memory.consolidation.models import ArchivalEntry
from synthorg.memory.models import MemoryEntry, MemoryMetadata, MemoryQuery
from synthorg.memory.org.models import OrgFactAuthor, OrgFactWriteRequest

# ── Fake Backends ──────────────────────────────────────────────


class FakeMemoryBackend:
    """Fake memory backend for testing archival."""

    def __init__(self, entries: tuple[MemoryEntry, ...] = ()) -> None:
        self._entries = list(entries)
        self._deleted: list[str] = []

    async def retrieve(
        self,
        agent_id: NotBlankStr,
        query: MemoryQuery,
    ) -> tuple[MemoryEntry, ...]:
        return tuple(e for e in self._entries if e.agent_id == agent_id)

    async def delete(
        self,
        agent_id: NotBlankStr,
        memory_id: NotBlankStr,
    ) -> bool:
        self._deleted.append(str(memory_id))
        return True


class FakeFailingMemoryBackend:
    """Fake memory backend that fails on retrieve."""

    async def retrieve(
        self,
        agent_id: NotBlankStr,
        query: MemoryQuery,
    ) -> tuple[MemoryEntry, ...]:
        msg = "Connection lost"
        raise OSError(msg)

    async def delete(
        self,
        agent_id: NotBlankStr,
        memory_id: NotBlankStr,
    ) -> bool:
        return True


class FakeArchivalStore:
    """Fake archival store that records archived entries."""

    def __init__(self) -> None:
        self.archived: list[ArchivalEntry] = []

    async def archive(self, entry: ArchivalEntry) -> NotBlankStr:
        self.archived.append(entry)
        return entry.original_id


class FakeFailingArchivalStore:
    """Fake archival store that fails on archive."""

    async def archive(self, entry: ArchivalEntry) -> NotBlankStr:
        msg = "Storage full"
        raise OSError(msg)


class FakeOrgMemoryBackend:
    """Fake org memory backend that records written facts."""

    def __init__(self) -> None:
        self.written: list[tuple[OrgFactWriteRequest, OrgFactAuthor]] = []

    async def write(
        self,
        request: OrgFactWriteRequest,
        *,
        author: OrgFactAuthor,
    ) -> NotBlankStr:
        self.written.append((request, author))
        return NotBlankStr("org-fact-001")


# ── Helpers ────────────────────────────────────────────────────


def _make_memory_entry(
    *,
    entry_id: str = "mem-001",
    agent_id: str = "agent-001",
    category: MemoryCategory = MemoryCategory.SEMANTIC,
    content: str = "Some knowledge",
) -> MemoryEntry:
    """Build a MemoryEntry for tests."""
    now = datetime.now(UTC)
    return MemoryEntry(
        id=entry_id,
        agent_id=agent_id,
        category=category,
        content=content,
        metadata=MemoryMetadata(),
        created_at=now,
    )


# ── Tests ──────────────────────────────────────────────────────


@pytest.mark.unit
class TestFullSnapshotStrategy:
    """FullSnapshotStrategy.archive tests."""

    async def test_strategy_name(self) -> None:
        strategy = FullSnapshotStrategy()
        assert strategy.name == "full_snapshot"

    async def test_full_pipeline_with_promotion(self) -> None:
        semantic = _make_memory_entry(
            entry_id="mem-s",
            category=MemoryCategory.SEMANTIC,
            content="Semantic knowledge",
        )
        procedural = _make_memory_entry(
            entry_id="mem-p",
            category=MemoryCategory.PROCEDURAL,
            content="Procedural knowledge",
        )
        episodic = _make_memory_entry(
            entry_id="mem-e",
            category=MemoryCategory.EPISODIC,
            content="Episodic memory",
        )

        memory_backend = FakeMemoryBackend(
            entries=(semantic, procedural, episodic),
        )
        archival_store = FakeArchivalStore()
        org_backend = FakeOrgMemoryBackend()

        # Patch OrgFactAuthor to bypass seniority validation so
        # the promotion path is exercised end-to-end.
        stub_author = OrgFactAuthor(
            agent_id=NotBlankStr("agent-001"),
            seniority=SeniorityLevel.MID,
        )
        strategy = FullSnapshotStrategy()
        with patch(
            "synthorg.hr.full_snapshot_strategy.OrgFactAuthor",
            return_value=stub_author,
        ):
            result = await strategy.archive(
                agent_id=NotBlankStr("agent-001"),
                memory_backend=memory_backend,  # type: ignore[arg-type]
                archival_store=archival_store,  # type: ignore[arg-type]
                org_memory_backend=org_backend,  # type: ignore[arg-type]
                agent_seniority=SeniorityLevel.MID,
            )

        assert result.total_archived == 3
        # SEMANTIC and PROCEDURAL are promotable.
        assert result.promoted_to_org == 2
        assert result.hot_store_cleaned is True
        assert result.strategy_name == "full_snapshot"
        assert len(archival_store.archived) == 3
        assert len(org_backend.written) == 2

    async def test_empty_memories_zero_archived(self) -> None:
        memory_backend = FakeMemoryBackend(entries=())
        archival_store = FakeArchivalStore()

        strategy = FullSnapshotStrategy()
        result = await strategy.archive(
            agent_id=NotBlankStr("agent-001"),
            memory_backend=memory_backend,  # type: ignore[arg-type]
            archival_store=archival_store,  # type: ignore[arg-type]
        )

        assert result.total_archived == 0
        assert result.promoted_to_org == 0
        assert result.hot_store_cleaned is True

    async def test_no_org_memory_backend_skip_promotion(self) -> None:
        semantic = _make_memory_entry(
            entry_id="mem-s",
            category=MemoryCategory.SEMANTIC,
        )
        memory_backend = FakeMemoryBackend(entries=(semantic,))
        archival_store = FakeArchivalStore()

        strategy = FullSnapshotStrategy()
        result = await strategy.archive(
            agent_id=NotBlankStr("agent-001"),
            memory_backend=memory_backend,  # type: ignore[arg-type]
            archival_store=archival_store,  # type: ignore[arg-type]
            org_memory_backend=None,
        )

        assert result.total_archived == 1
        assert result.promoted_to_org == 0

    async def test_per_entry_archive_failure_continues(self) -> None:
        entry = _make_memory_entry(entry_id="mem-fail")
        memory_backend = FakeMemoryBackend(entries=(entry,))
        failing_store = FakeFailingArchivalStore()

        strategy = FullSnapshotStrategy()
        result = await strategy.archive(
            agent_id=NotBlankStr("agent-001"),
            memory_backend=memory_backend,  # type: ignore[arg-type]
            archival_store=failing_store,  # type: ignore[arg-type]
        )

        # Entry archival failed; count should be 0.
        assert result.total_archived == 0
        assert result.promoted_to_org == 0

    async def test_retrieve_failure_raises_archival_error(self) -> None:
        failing_backend = FakeFailingMemoryBackend()
        archival_store = FakeArchivalStore()

        strategy = FullSnapshotStrategy()
        with pytest.raises(MemoryArchivalError, match="Failed to retrieve"):
            await strategy.archive(
                agent_id=NotBlankStr("agent-001"),
                memory_backend=failing_backend,  # type: ignore[arg-type]
                archival_store=archival_store,  # type: ignore[arg-type]
            )

    async def test_delete_failure_results_in_hot_store_not_cleaned(self) -> None:
        """Memory backend delete failure -> hot_store_cleaned=False."""
        entry = _make_memory_entry(entry_id="mem-del-fail")

        class FailingDeleteBackend:
            async def retrieve(
                self,
                agent_id: NotBlankStr,
                query: MemoryQuery,
            ) -> tuple[MemoryEntry, ...]:
                return (entry,)

            async def delete(
                self,
                agent_id: NotBlankStr,
                memory_id: NotBlankStr,
            ) -> bool:
                msg = "disk error during delete"
                raise OSError(msg)

        backend = FailingDeleteBackend()
        archival_store = FakeArchivalStore()

        strategy = FullSnapshotStrategy()
        result = await strategy.archive(
            agent_id=NotBlankStr("agent-001"),
            memory_backend=backend,  # type: ignore[arg-type]
            archival_store=archival_store,  # type: ignore[arg-type]
        )

        assert result.total_archived > 0
        assert result.hot_store_cleaned is False

    async def test_non_promotable_categories_not_promoted(self) -> None:
        working = _make_memory_entry(
            entry_id="mem-w",
            category=MemoryCategory.WORKING,
            content="Working memory",
        )
        social = _make_memory_entry(
            entry_id="mem-soc",
            category=MemoryCategory.SOCIAL,
            content="Social memory",
        )
        memory_backend = FakeMemoryBackend(entries=(working, social))
        archival_store = FakeArchivalStore()
        org_backend = FakeOrgMemoryBackend()

        strategy = FullSnapshotStrategy()
        result = await strategy.archive(
            agent_id=NotBlankStr("agent-001"),
            memory_backend=memory_backend,  # type: ignore[arg-type]
            archival_store=archival_store,  # type: ignore[arg-type]
            org_memory_backend=org_backend,  # type: ignore[arg-type]
        )

        assert result.total_archived == 2
        assert result.promoted_to_org == 0
        assert len(org_backend.written) == 0
