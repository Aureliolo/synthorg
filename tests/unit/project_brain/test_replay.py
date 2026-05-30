"""Unit tests for :func:`synthorg.project_brain.replay.reindex_unindexed`.

Validates the gap-tracked boot recovery: an entry persisted but missing from
(or stale in) the RAG index is re-indexed and marked, while already-current
entries are left untouched.
"""

from datetime import UTC, datetime

import pytest

from synthorg.core.enums import MemoryCategory
from synthorg.core.types import NotBlankStr
from synthorg.memory.backends.inmemory.adapter import InMemoryBackend
from synthorg.memory.models import MemoryQuery
from synthorg.project_brain.chunker import BrainChunker
from synthorg.project_brain.constants import (
    BRAIN_ENTRY_TAG_PREFIX,
    BRAIN_MEMORY_NAMESPACE,
    BRAIN_PROJECT_TAG_PREFIX,
    SYSTEM_BRAIN_AGENT_ID,
)
from synthorg.project_brain.indexer import BrainIndexer
from synthorg.project_brain.models import (
    BrainEntry,
    BrainEntryKind,
    BrainEntryStatus,
    DecisionPayload,
)
from synthorg.project_brain.replay import reindex_unindexed
from tests.unit.api.fakes import FakeProjectBrainRepository

pytestmark = pytest.mark.unit

_PROJECT = NotBlankStr("proj-1")


def _entry(entry_id: str, minute: int) -> BrainEntry:
    return BrainEntry(
        project_id=_PROJECT,
        entry_id=NotBlankStr(entry_id),
        revision=1,
        entry_kind=BrainEntryKind.DECISION,
        title=f"Decision {entry_id}",
        rationale="why",
        status=BrainEntryStatus.ACCEPTED,
        author=NotBlankStr("alice"),
        recorded_at=datetime(2026, 5, 30, 12, minute, 0, tzinfo=UTC),
        payload=DecisionPayload(decision_outcome=NotBlankStr("x")),
    )


async def _indexed_entry_ids(backend: InMemoryBackend) -> set[str]:
    entries = await backend.retrieve(
        SYSTEM_BRAIN_AGENT_ID,
        MemoryQuery(
            text=None,
            categories=frozenset({MemoryCategory.PROJECT_BRAIN}),
            namespaces=frozenset({BRAIN_MEMORY_NAMESPACE}),
            tags=(NotBlankStr(f"{BRAIN_PROJECT_TAG_PREFIX}{_PROJECT}"),),
            limit=1000,
        ),
    )
    ids: set[str] = set()
    for entry in entries:
        for tag in entry.metadata.tags:
            if tag.startswith(BRAIN_ENTRY_TAG_PREFIX):
                ids.add(tag[len(BRAIN_ENTRY_TAG_PREFIX) :])
    return ids


async def test_replay_reindexes_only_the_gap() -> None:
    """An entry absent from the index is re-indexed; an indexed one is skipped."""
    backend = InMemoryBackend()
    await backend.connect()
    repo = FakeProjectBrainRepository()
    chunker = BrainChunker()
    indexer = BrainIndexer(backend=backend)

    # e1 is persisted AND indexed (the happy path); e2 is persisted but its
    # index write "failed" (never indexed, never marked) -- the gap.
    e1 = await repo.append_with_next_revision(_entry("e1", 0))
    await indexer.index(
        project_id=_PROJECT,
        entry_id=e1.entry_id,
        chunks=chunker.chunk(project_id=_PROJECT, entry=e1),
    )
    await repo.mark_indexed(_PROJECT, e1.entry_id, e1.revision)
    e2 = await repo.append_with_next_revision(_entry("e2", 1))

    assert await _indexed_entry_ids(backend) == {e1.entry_id}

    reindexed = await reindex_unindexed(
        repo=repo, chunker=chunker, indexer=indexer, project_ids=(_PROJECT,)
    )

    assert reindexed == 1
    assert await _indexed_entry_ids(backend) == {e1.entry_id, e2.entry_id}
    # The gap entry is now marked indexed at its current revision.
    indexed_map = await repo.indexed_revisions(_PROJECT)
    assert indexed_map == {e1.entry_id: 1, e2.entry_id: 1}

    await backend.disconnect()


async def test_replay_noop_when_all_current() -> None:
    """Replay re-indexes nothing when every entry is already current."""
    backend = InMemoryBackend()
    await backend.connect()
    repo = FakeProjectBrainRepository()
    chunker = BrainChunker()
    indexer = BrainIndexer(backend=backend)

    entry = await repo.append_with_next_revision(_entry("e1", 0))
    await repo.mark_indexed(_PROJECT, entry.entry_id, entry.revision)

    reindexed = await reindex_unindexed(
        repo=repo, chunker=chunker, indexer=indexer, project_ids=(_PROJECT,)
    )
    assert reindexed == 0

    await backend.disconnect()
