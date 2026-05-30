"""Conformance tests for the project-brain repository (SQLite + Postgres).

Exercises the append-only revision model, the current-state projection (window
function), the atomic revision compare-and-set uniqueness, guarded retention,
and FK cascade. Runs against both backends via the shared ``backend`` fixture.
"""

from datetime import UTC, datetime

import pytest

from synthorg.core.project import Project
from synthorg.core.types import NotBlankStr
from synthorg.persistence.project_brain_protocol import BrainFilterSpec
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.project_brain.errors import BrainEntryRevisionConflictError
from synthorg.project_brain.models import (
    BrainEntry,
    BrainEntryKind,
    BrainEntryStatus,
    DecisionPayload,
    OpenQuestionPayload,
)

pytestmark = pytest.mark.integration


def _ts(minute: int = 0) -> datetime:
    return datetime(2026, 5, 30, 12, minute, 0, tzinfo=UTC)


def _project(project_id: str = "proj-1") -> Project:
    return Project(id=NotBlankStr(project_id), name=NotBlankStr("Demo"))


def _decision(
    project_id: str = "proj-1",
    *,
    entry_id: str | None = None,
    status: BrainEntryStatus = BrainEntryStatus.ACCEPTED,
    minute: int = 0,
    title: str = "Use append-only storage",
) -> BrainEntry:
    fields: dict[str, object] = {
        "project_id": NotBlankStr(project_id),
        "revision": 1,
        "entry_kind": BrainEntryKind.DECISION,
        "title": title,
        "rationale": "Full why/when history is required.",
        "status": status,
        "author": NotBlankStr("agent_alice"),
        "recorded_at": _ts(minute),
        "payload": DecisionPayload(decision_outcome="append-only"),
    }
    if entry_id is not None:
        fields["entry_id"] = NotBlankStr(entry_id)
    return BrainEntry(**fields)  # type: ignore[arg-type]


class TestProjectBrainRepository:
    """Conformance for the project-brain repository across backends."""

    async def test_append_with_revision_and_get(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.projects.save(_project())
        repo = backend.project_brain
        stored = await repo.append_with_next_revision(_decision())
        assert stored.revision == 1
        fetched = await repo.get((stored.project_id, stored.entry_id, 1))
        assert fetched is not None
        assert fetched.entry_id == stored.entry_id
        assert isinstance(fetched.payload, DecisionPayload)

    async def test_next_revision_increments_per_entry(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.projects.save(_project())
        repo = backend.project_brain
        first = await repo.append_with_next_revision(_decision())
        second = await repo.append_with_next_revision(
            first.model_copy(
                update={"status": BrainEntryStatus.SUPERSEDED, "recorded_at": _ts(5)}
            )
        )
        assert second.revision == 2
        assert second.entry_id == first.entry_id

    async def test_get_current_returns_latest(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.projects.save(_project())
        repo = backend.project_brain
        first = await repo.append_with_next_revision(_decision())
        await repo.append_with_next_revision(
            first.model_copy(
                update={"status": BrainEntryStatus.SUPERSEDED, "recorded_at": _ts(5)}
            )
        )
        current = await repo.get_current(first.project_id, first.entry_id)
        assert current is not None
        assert current.revision == 2
        assert current.status is BrainEntryStatus.SUPERSEDED

    async def test_list_current_returns_one_row_per_entry(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.projects.save(_project())
        repo = backend.project_brain
        first = await repo.append_with_next_revision(_decision(minute=0))
        await repo.append_with_next_revision(
            first.model_copy(
                update={"status": BrainEntryStatus.SUPERSEDED, "recorded_at": _ts(5)}
            )
        )
        await repo.append_with_next_revision(
            _decision(title="Second decision", minute=2)
        )
        spec = BrainFilterSpec(project_id=NotBlankStr("proj-1"))
        current = await repo.list_current(spec)
        assert len(current) == 2
        by_id = {e.entry_id: e for e in current}
        assert by_id[first.entry_id].revision == 2

    async def test_list_current_filters_by_status(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.projects.save(_project())
        repo = backend.project_brain
        await repo.append_with_next_revision(
            _decision(status=BrainEntryStatus.ACCEPTED)
        )
        await repo.append_with_next_revision(
            BrainEntry(
                project_id=NotBlankStr("proj-1"),
                revision=1,
                entry_kind=BrainEntryKind.OPEN_QUESTION,
                title="Which queue?",
                rationale="Throughput target unclear.",
                status=BrainEntryStatus.OPEN,
                author=NotBlankStr("agent_bob"),
                recorded_at=_ts(3),
                payload=OpenQuestionPayload(),
            )
        )
        spec = BrainFilterSpec(
            project_id=NotBlankStr("proj-1"),
            status=BrainEntryStatus.OPEN,
        )
        only_open = await repo.list_current(spec)
        assert len(only_open) == 1
        assert only_open[0].entry_kind is BrainEntryKind.OPEN_QUESTION

    async def test_history_oldest_first(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project())
        repo = backend.project_brain
        first = await repo.append_with_next_revision(_decision())
        await repo.append_with_next_revision(
            first.model_copy(
                update={"status": BrainEntryStatus.SUPERSEDED, "recorded_at": _ts(5)}
            )
        )
        chain = await repo.history(first.project_id, first.entry_id)
        assert [e.revision for e in chain] == [1, 2]

    async def test_duplicate_revision_conflicts(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.projects.save(_project())
        repo = backend.project_brain
        stored = await repo.append_with_next_revision(_decision())
        with pytest.raises(BrainEntryRevisionConflictError):
            await repo.append(stored)

    async def test_count_counts_current_state(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.projects.save(_project())
        repo = backend.project_brain
        first = await repo.append_with_next_revision(_decision())
        await repo.append_with_next_revision(
            first.model_copy(
                update={"status": BrainEntryStatus.SUPERSEDED, "recorded_at": _ts(5)}
            )
        )
        spec = BrainFilterSpec(project_id=NotBlankStr("proj-1"))
        assert await repo.count(spec) == 1

    async def test_purge_retains_latest_revision(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.projects.save(_project())
        repo = backend.project_brain
        first = await repo.append_with_next_revision(_decision(minute=0))
        await repo.append_with_next_revision(
            first.model_copy(
                update={"status": BrainEntryStatus.SUPERSEDED, "recorded_at": _ts(30)}
            )
        )
        removed = await repo.purge_before(_ts(20))
        assert removed == 1
        current = await repo.get_current(first.project_id, first.entry_id)
        assert current is not None
        assert current.revision == 2

    async def test_fk_cascade_on_project_delete(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.projects.save(_project())
        repo = backend.project_brain
        stored = await repo.append_with_next_revision(_decision())
        await backend.projects.delete(stored.project_id)
        current = await repo.get_current(stored.project_id, stored.entry_id)
        assert current is None

    async def test_query_returns_all_revisions_newest_first(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.projects.save(_project())
        repo = backend.project_brain
        first = await repo.append_with_next_revision(_decision(minute=0))
        await repo.append_with_next_revision(
            first.model_copy(
                update={"status": BrainEntryStatus.SUPERSEDED, "recorded_at": _ts(5)}
            )
        )
        spec = BrainFilterSpec(project_id=NotBlankStr("proj-1"))
        rows = await repo.query(spec)
        assert len(rows) == 2
        assert [e.revision for e in rows] == [2, 1]

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        await backend.projects.save(_project())
        repo = backend.project_brain
        missing = await repo.get((NotBlankStr("proj-1"), NotBlankStr("nope"), 1))
        assert missing is None
