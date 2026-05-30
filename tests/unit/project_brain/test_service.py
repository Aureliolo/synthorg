"""Unit tests for :class:`synthorg.project_brain.service.ProjectBrainService`."""

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.project_brain.errors import (
    BrainEntryNotFoundError,
    BrainEntryValidationError,
)
from synthorg.project_brain.models import (
    BlockerPayload,
    BlockerSeverity,
    BrainEntry,
    BrainEntryKind,
    BrainEntryStatus,
    DecisionPayload,
    DependencyKind,
    DependencyPayload,
    OpenQuestionPayload,
    PlanRevisionPayload,
)
from synthorg.project_brain.service import ProjectBrainService
from tests.unit.api.fakes import FakeProjectBrainRepository
from tests.unit.project_brain.conftest import FakeBrainWriter

pytestmark = pytest.mark.unit

_PROJECT = NotBlankStr("proj-1")
_AUTHOR = NotBlankStr("agent_alice")


async def _decision(service: ProjectBrainService) -> BrainEntry:
    return await service.append_entry(
        project_id=_PROJECT,
        title=NotBlankStr("Adopt append-only storage"),
        rationale=NotBlankStr("History matters."),
        status=BrainEntryStatus.ACCEPTED,
        author=_AUTHOR,
        payload=DecisionPayload(decision_outcome=NotBlankStr("append-only")),
    )


async def test_append_entry_creates_revision_one(
    brain_service: ProjectBrainService,
) -> None:
    entry = await _decision(brain_service)
    assert entry.revision == 1
    assert entry.status is BrainEntryStatus.ACCEPTED


async def test_append_marks_entry_indexed(
    brain_service: ProjectBrainService,
    brain_repo: FakeProjectBrainRepository,
) -> None:
    entry = await _decision(brain_service)
    indexed = await brain_repo.indexed_revisions(_PROJECT)
    assert indexed == {entry.entry_id: 1}


async def test_revise_entry_increments_revision(
    brain_service: ProjectBrainService,
) -> None:
    entry = await _decision(brain_service)
    revised = await brain_service.revise_entry(
        project_id=_PROJECT,
        entry_id=entry.entry_id,
        author=_AUTHOR,
        rationale=NotBlankStr("Updated reasoning."),
    )
    assert revised.revision == 2
    assert revised.rationale == "Updated reasoning."


async def test_resolve_open_question_records_answer(
    brain_service: ProjectBrainService,
) -> None:
    entry = await brain_service.append_entry(
        project_id=_PROJECT,
        title=NotBlankStr("Which queue?"),
        rationale=NotBlankStr("Throughput unclear."),
        status=BrainEntryStatus.OPEN,
        author=_AUTHOR,
        payload=OpenQuestionPayload(),
    )
    resolved = await brain_service.resolve(
        project_id=_PROJECT,
        entry_id=entry.entry_id,
        author=_AUTHOR,
        answer=NotBlankStr("Use the durable queue."),
    )
    assert resolved.status is BrainEntryStatus.RESOLVED
    assert isinstance(resolved.payload, OpenQuestionPayload)
    assert resolved.payload.answer == "Use the durable queue."


async def test_resolve_dependency_moves_to_resolved(
    brain_service: ProjectBrainService,
) -> None:
    entry = await brain_service.append_entry(
        project_id=_PROJECT,
        title=NotBlankStr("Needs auth service"),
        rationale=NotBlankStr("Blocked on upstream."),
        status=BrainEntryStatus.OPEN,
        author=_AUTHOR,
        payload=DependencyPayload(
            depends_on=NotBlankStr("auth-svc"),
            dependency_kind=DependencyKind.TASK,
        ),
    )
    resolved = await brain_service.resolve(
        project_id=_PROJECT, entry_id=entry.entry_id, author=_AUTHOR
    )
    assert resolved.status is BrainEntryStatus.RESOLVED


async def test_resolve_rejects_non_resolvable_kind(
    brain_service: ProjectBrainService,
) -> None:
    entry = await _decision(brain_service)
    with pytest.raises(BrainEntryValidationError):
        await brain_service.resolve(
            project_id=_PROJECT, entry_id=entry.entry_id, author=_AUTHOR
        )


async def test_clear_blocker_sets_resolution(
    brain_service: ProjectBrainService,
) -> None:
    entry = await brain_service.append_entry(
        project_id=_PROJECT,
        title=NotBlankStr("CI is down"),
        rationale=NotBlankStr("Runner offline."),
        status=BrainEntryStatus.BLOCKED,
        author=_AUTHOR,
        payload=BlockerPayload(severity=BlockerSeverity.HIGH),
    )
    cleared = await brain_service.clear_blocker(
        project_id=_PROJECT,
        entry_id=entry.entry_id,
        author=_AUTHOR,
        resolution=NotBlankStr("Restarted the runner."),
    )
    assert cleared.status is BrainEntryStatus.CLEARED
    assert isinstance(cleared.payload, BlockerPayload)
    assert cleared.payload.resolution == "Restarted the runner."
    assert cleared.payload.severity is BlockerSeverity.HIGH


async def test_supersede_links_successor(
    brain_service: ProjectBrainService,
) -> None:
    entry = await _decision(brain_service)
    superseded = await brain_service.supersede(
        project_id=_PROJECT,
        entry_id=entry.entry_id,
        by_entry_id=NotBlankStr("successor-1"),
        author=_AUTHOR,
    )
    assert superseded.status is BrainEntryStatus.SUPERSEDED
    assert NotBlankStr("successor-1") in superseded.related_entry_ids


async def test_supersede_rejects_non_supersedable_kind(
    brain_service: ProjectBrainService,
) -> None:
    entry = await brain_service.append_entry(
        project_id=_PROJECT,
        title=NotBlankStr("CI is down"),
        rationale=NotBlankStr("Runner offline."),
        status=BrainEntryStatus.BLOCKED,
        author=_AUTHOR,
        payload=BlockerPayload(severity=BlockerSeverity.HIGH),
    )
    with pytest.raises(BrainEntryValidationError):
        await brain_service.supersede(
            project_id=_PROJECT,
            entry_id=entry.entry_id,
            by_entry_id=NotBlankStr("x"),
            author=_AUTHOR,
        )


async def test_snapshot_failure_does_not_fail_append(
    brain_service: ProjectBrainService,
    brain_writer: FakeBrainWriter,
) -> None:
    """A best-effort snapshot failure must not fail the durable append."""
    brain_writer.fail = True
    entry = await _decision(brain_service)
    assert entry.revision == 1
    # The entry is still durably persisted and queryable.
    current = await brain_service.get_current(
        project_id=_PROJECT,
        entry_id=entry.entry_id,
    )
    assert current is not None


async def test_list_current_and_count(
    brain_service: ProjectBrainService,
) -> None:
    await _decision(brain_service)
    await brain_service.append_entry(
        project_id=_PROJECT,
        title=NotBlankStr("Plan v2"),
        rationale=NotBlankStr("Re-scoped."),
        status=BrainEntryStatus.ACTIVE,
        author=_AUTHOR,
        payload=PlanRevisionPayload(summary=NotBlankStr("Cut scope to MVP.")),
    )
    summaries = await brain_service.list_current(project_id=_PROJECT)
    assert len(summaries) == 2
    decisions = await brain_service.list_current(
        project_id=_PROJECT, entry_kind=BrainEntryKind.DECISION
    )
    assert len(decisions) == 1
    assert await brain_service.count_current(project_id=_PROJECT) == 2


async def test_history_returns_chain_and_missing_raises(
    brain_service: ProjectBrainService,
) -> None:
    entry = await _decision(brain_service)
    await brain_service.revise_entry(
        project_id=_PROJECT,
        entry_id=entry.entry_id,
        author=_AUTHOR,
        status=BrainEntryStatus.SUPERSEDED,
    )
    chain = await brain_service.history(
        project_id=_PROJECT,
        entry_id=entry.entry_id,
    )
    assert [e.revision for e in chain] == [1, 2]
    with pytest.raises(BrainEntryNotFoundError):
        await brain_service.history(project_id=_PROJECT, entry_id=NotBlankStr("nope"))


async def test_get_entry_missing_raises(
    brain_service: ProjectBrainService,
) -> None:
    with pytest.raises(BrainEntryNotFoundError):
        await brain_service.get_entry(project_id=_PROJECT, entry_id=NotBlankStr("nope"))
