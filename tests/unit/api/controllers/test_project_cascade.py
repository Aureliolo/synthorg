"""Unit tests for the project-delete cascade's contended plan retirement.

The rollup advances the same plan row whenever a task under it changes, so a
delete issued while the last task completes can lose the race. What matters is
not only that the retry happens but that it re-decides: an itemless shell can
only be FAILED, a filled plan can only be SUPERSEDED, and the race winner is
exactly what turns one into the other.

The removal pass that follows has its own question: it retires a plan's review
approval around the delete, so a plan the delete leaves standing is owed that
approval back.

And every child the pass gets past has to leave a tombstone behind, removed
here or found already gone, because a row that has left the page cannot be
recorded by a later attempt.
"""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, NamedTuple
from unittest.mock import AsyncMock

import pytest

from synthorg.api.controllers._project_cascade import (
    _SUPERSEDE_ATTEMPTS,
    _delete_cancelled_tasks,
    _delete_retired_plans,
    _supersede_plan,
)
from synthorg.api.lifecycle_helpers.plan_questions import PLAN_ID_METADATA_KEY
from synthorg.api.services.plan_service import PlanService
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalSource, ApprovalStatus
from synthorg.approval.state import ApprovalStateSlice
from synthorg.core.approval import ApprovalItem
from synthorg.core.deleted_entity import DeletedEntity, DeletedEntityKind
from synthorg.core.domain_errors import ConflictError, VersionConflictError
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import TaskNotFoundError
from synthorg.engine.task_engine import TaskEngine
from synthorg.persistence.deleted_entity_protocol import DeletedEntityFilterSpec
from synthorg.persistence.plan_protocol import PlanDeleteOutcome, PlanRepository
from tests._shared import as_uuid, make_app_state, mock_of, sid
from tests.unit.api.fakes_backend import FakePersistenceBackend

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 8, 1, 9, 0, tzinfo=UTC)

#: Configured ``mock_of`` instance, typed loosely so the ``unittest.mock``
#: assertion API type-checks.
_Configured = Any  # type: ignore[explicit-any]


def _plan(*, status: PlanStatus, filled: bool, version: int = 1) -> Plan:
    items = (
        (
            PlanItem(
                id=NotBlankStr(sid("item-1")),
                title=NotBlankStr("Build it"),
                description=NotBlankStr("Implement the board."),
                acceptance_criteria=(NotBlankStr("it renders"),),
                expected_artifacts=(NotBlankStr("web/src/board.tsx"),),
            ),
        )
        if filled
        else ()
    )
    return Plan(
        id=as_uuid("plan-1"),
        project=NotBlankStr(sid("proj-1")),
        project_name=NotBlankStr("Games"),
        objective_id=NotBlankStr("obj-1"),
        objective_title=NotBlankStr("Ship the board"),
        parent_task_id=NotBlankStr(sid("task-1")),
        items=items,
        status=status,
        version=version,
        created_at=_NOW,
        updated_at=_NOW,
    )


class TestSupersedeUnderContention:
    async def test_the_terminal_is_re_derived_from_the_race_winners_plan(
        self,
    ) -> None:
        """A shell filled by the race winner is superseded, not failed.

        The first attempt sees an itemless PLANNING shell, whose only legal
        terminal is FAILED. It loses to the decomposer, which fills the items
        and parks it for review. Re-deciding is what makes the retry legal:
        FAILED against a filled plan would be wrong, and SUPERSEDED against
        the shell violates the items CHECK.
        """
        shell = _plan(status=PlanStatus.PLANNING, filled=False)
        filled = _plan(status=PlanStatus.PENDING_REVIEW, filled=True, version=2)
        service: _Configured = mock_of[PlanService](
            sync_status=AsyncMock(side_effect=[VersionConflictError("lost"), filled])
        )
        repository: _Configured = mock_of[PlanRepository](
            get=AsyncMock(return_value=filled)
        )

        await _supersede_plan(service, repository, shell, requested_by="admin")

        first, second = service.sync_status.await_args_list
        assert first.args[1] is PlanStatus.FAILED
        assert first.kwargs["failure_reason"] == "project deleted"
        assert second.args[0] is filled
        assert second.args[1] is PlanStatus.SUPERSEDED
        # SUPERSEDED forbids a failure_reason, so re-deciding has to drop it.
        assert second.kwargs["failure_reason"] is None

    async def test_a_winner_that_already_retired_it_stops(self) -> None:
        # Nothing is left orphaned, so a second write would only lose another
        # race, and forcing a terminal over a terminal is not the cascade's
        # decision to make.
        live = _plan(status=PlanStatus.PENDING_REVIEW, filled=True)
        retired = _plan(status=PlanStatus.SUPERSEDED, filled=True, version=2)
        service: _Configured = mock_of[PlanService](
            sync_status=AsyncMock(side_effect=VersionConflictError("lost"))
        )
        repository: _Configured = mock_of[PlanRepository](
            get=AsyncMock(return_value=retired)
        )

        await _supersede_plan(service, repository, live, requested_by="admin")

        assert service.sync_status.await_count == 1

    async def test_a_plan_the_winner_deleted_stops(self) -> None:
        # The row is gone, so there is nothing to orphan and nothing to write.
        live = _plan(status=PlanStatus.PENDING_REVIEW, filled=True)
        service: _Configured = mock_of[PlanService](
            sync_status=AsyncMock(side_effect=VersionConflictError("lost"))
        )
        repository: _Configured = mock_of[PlanRepository](
            get=AsyncMock(return_value=None)
        )

        await _supersede_plan(service, repository, live, requested_by="admin")

        assert service.sync_status.await_count == 1

    async def test_an_exhausted_budget_aborts_the_delete(self) -> None:
        """Returning quietly would let the caller delete over a live plan.

        The caller removes the project once the cascade reports done, and
        ``plans.project`` carries no foreign key, so the plan would survive
        pointing at nothing. Contention is transient, so refusing this
        delete is what the operator can act on.
        """
        live = _plan(status=PlanStatus.PENDING_REVIEW, filled=True)
        service: _Configured = mock_of[PlanService](
            sync_status=AsyncMock(side_effect=VersionConflictError("lost"))
        )
        repository: _Configured = mock_of[PlanRepository](
            get=AsyncMock(return_value=live)
        )

        with pytest.raises(ConflictError):
            await _supersede_plan(service, repository, live, requested_by="admin")

        assert service.sync_status.await_count == _SUPERSEDE_ATTEMPTS


def _review_approval(plan: Plan) -> ApprovalItem:
    """Build the pending review approval the teardown has to decide about."""
    return ApprovalItem(
        id=as_uuid("review-held"),
        action_type=NotBlankStr("plan:approve"),
        title=NotBlankStr("Approve plan"),
        description=NotBlankStr("1 subtask(s)"),
        requested_by=NotBlankStr("user-1"),
        risk_level=ApprovalRiskLevel.MEDIUM,
        source=ApprovalSource.PLAN_REVIEW,
        status=ApprovalStatus.PENDING,
        created_at=_NOW,
        metadata={PLAN_ID_METADATA_KEY: str(plan.id)},
    )


class _RecordingApprovalStore:
    """Approval store double keeping whatever the retirement last wrote."""

    def __init__(self, items: tuple[ApprovalItem, ...]) -> None:
        self._items = {str(item.id): item for item in items}

    async def list_items(self, **_: object) -> tuple[ApprovalItem, ...]:
        return tuple(self._items.values())

    async def get(self, approval_id: object) -> ApprovalItem | None:
        return self._items.get(str(approval_id))

    async def save(self, item: ApprovalItem) -> ApprovalItem | None:
        self._items[str(item.id)] = item
        return item

    async def save_if_pending(self, item: ApprovalItem) -> ApprovalItem | None:
        self._items[str(item.id)] = item
        return item


class _RemovalPass(NamedTuple):
    """One project's plan page, prepared but not yet run."""

    run: Callable[[], Awaitable[int]]
    store: _RecordingApprovalStore
    plan: Plan
    backend: FakePersistenceBackend


async def _removal_pass(outcome: PlanDeleteOutcome) -> _RemovalPass:
    """Prepare one project's plan page with a delete answering *outcome*.

    Returns the pass unrun so a caller can assert on the store after a
    refusal, which raises, and can run it twice to model a retried teardown.

    Returns:
        The pass, the stores it will write, and the plan under it.
    """
    plan = _plan(status=PlanStatus.SUPERSEDED, filled=True)
    backend = FakePersistenceBackend()
    backend.mark_connected()
    await backend.plans.save(plan)
    store = _RecordingApprovalStore((_review_approval(plan),))
    service: _Configured = mock_of[PlanService](
        delete_for_project_teardown=AsyncMock(return_value=outcome)
    )
    app_state = make_app_state(
        persistence=backend,
        slices={ApprovalStateSlice: {"store": store}},
    )

    async def _run() -> int:
        return await _delete_retired_plans(
            app_state,
            service,
            NotBlankStr(sid("proj-1")),
            requested_by="user-1",
        )

    return _RemovalPass(_run, store, plan, backend)


async def _run_removal_pass(
    outcome: PlanDeleteOutcome,
) -> tuple[int, _RecordingApprovalStore]:
    """Page one project's plans with a delete answering *outcome*.

    Returns:
        How many plans the pass removed, and the approval store it wrote.
    """
    prepared = await _removal_pass(outcome)
    return await prepared.run(), prepared.store


def _task_approval(task: Task) -> ApprovalItem:
    """Build a pending approval raised against *task*.

    Returns:
        The approval the teardown has to retire with the task.
    """
    return ApprovalItem(
        id=as_uuid("task-review-held"),
        action_type=NotBlankStr("task:review"),
        title=NotBlankStr("Review the deliverable"),
        description=NotBlankStr("1 artifact"),
        requested_by=NotBlankStr("user-1"),
        risk_level=ApprovalRiskLevel.MEDIUM,
        source=ApprovalSource.REVIEW_GATE,
        status=ApprovalStatus.PENDING,
        created_at=_NOW,
        task_id=NotBlankStr(str(task.id)),
    )


async def _run_task_removal_pass(
    refusal: Exception | None,
) -> tuple[int, tuple[DeletedEntity, ...], _RecordingApprovalStore]:
    """Page one project's tasks with a delete that raises *refusal*, or lands.

    Args:
        refusal: What ``delete_task`` raises, or ``None`` for a delete that
            removes the row.

    Returns:
        How many tasks the pass removed, the tombstones it filed, and the
        approval store it wrote.
    """
    task = Task(
        id=as_uuid("task-doomed"),
        title=NotBlankStr("Ship the board"),
        description=NotBlankStr("Implement it."),
        type=TaskType.DEVELOPMENT,
        project=NotBlankStr(sid("proj-1")),
        created_by=NotBlankStr("user-1"),
        status=TaskStatus.CANCELLED,
    )
    backend = FakePersistenceBackend()
    backend.mark_connected()
    await backend.tasks.save(task)
    store = _RecordingApprovalStore((_task_approval(task),))
    engine: _Configured = mock_of[TaskEngine](
        delete_task=AsyncMock(side_effect=refusal)
    )
    app_state = make_app_state(
        persistence=backend,
        slices={ApprovalStateSlice: {"store": store}},
    )

    removed = await _delete_cancelled_tasks(
        app_state,
        engine,
        NotBlankStr(sid("proj-1")),
        requested_by="user-1",
    )
    return removed, tuple(backend.deleted_entities.tombstones), store


class TestRemovalPassApprovalRetirement:
    """A plan the delete leaves standing stops the teardown and keeps its review.

    Retirement is scoped to the delete, and until now the only thing that
    undid it was an exception. Live work refuses this delete by returning,
    which left the approval expired against a plan still listed, still
    reviewable, and no longer answerable.
    """

    async def test_a_refused_plan_aborts_the_teardown(self) -> None:
        """Carrying on would delete the project out from under the plan.

        ``plans.project`` carries no foreign key, so a plan the teardown
        skipped survives naming an id that no longer resolves, reachable by
        no route. The refusal has to reach the operator, who retries.
        """
        prepared = await _removal_pass(
            PlanDeleteOutcome(deleted=False, live_task_count=1)
        )

        with pytest.raises(ConflictError, match=str(prepared.plan.id)):
            await prepared.run()

        assert [item.status for item in await prepared.store.list_items()] == [
            ApprovalStatus.PENDING
        ]

    async def test_a_plan_already_gone_keeps_its_review_retired(self) -> None:
        """Gone is gone, whoever removed it.

        Restoring here would put a pending question back against a plan that
        no longer resolves, which is the dangling approval the retirement
        exists to prevent.
        """
        deleted, store = await _run_removal_pass(
            PlanDeleteOutcome(deleted=False, live_task_count=0)
        )

        assert deleted == 0
        assert [item.status for item in await store.list_items()] == [
            ApprovalStatus.EXPIRED
        ]

    async def test_a_removed_plan_is_counted_and_stays_retired(self) -> None:
        deleted, store = await _run_removal_pass(PlanDeleteOutcome(deleted=True))

        assert deleted == 1
        assert [item.status for item in await store.list_items()] == [
            ApprovalStatus.EXPIRED
        ]


class TestRemovalPassRecordsWhatItPassed:
    """A child the teardown passes gets a tombstone, and exactly one."""

    async def test_a_plan_already_gone_is_still_recorded(self) -> None:
        """The already-gone branch is the record's only chance.

        Nothing retries a missing tombstone. The write is best-effort, so a
        failure parks nothing for an operator, and a plan that has gone no
        longer comes back from the page this loop reads, so a later attempt
        cannot supply what the earlier one lost. Skipping the branch left
        every surviving cost and decision row naming an id that resolved to
        nothing at all rather than to what it was.
        """
        prepared = await _removal_pass(
            PlanDeleteOutcome(deleted=False, live_task_count=0)
        )

        await prepared.run()

        (tomb,) = await prepared.backend.deleted_entities.query(
            DeletedEntityFilterSpec(entity_id=NotBlankStr(str(prepared.plan.id)))
        )
        assert tomb.entity_kind is DeletedEntityKind.PLAN
        assert tomb.display_name == prepared.plan.objective_title

    async def test_a_task_already_gone_is_recorded_but_not_counted(self) -> None:
        """The engine reporting it gone is the same record, a different count.

        ``tasks_deleted`` says what this pass took with it, so a task a
        concurrent delete already removed is not one of them. The tombstone is
        still owed: the row is out of the system either way, and the cost and
        decision rows that keep naming its id do not care which caller won.
        """
        removed, recorded, store = await _run_task_removal_pass(
            TaskNotFoundError("already gone")
        )

        assert removed == 0
        assert [t.entity_kind for t in recorded] == [DeletedEntityKind.TASK]
        assert [item.status for item in await store.list_items()] == [
            ApprovalStatus.EXPIRED
        ]

    async def test_a_task_this_pass_removed_is_counted_and_recorded(self) -> None:
        removed, recorded, store = await _run_task_removal_pass(None)

        assert removed == 1
        assert [t.entity_kind for t in recorded] == [DeletedEntityKind.TASK]
        assert [item.status for item in await store.list_items()] == [
            ApprovalStatus.EXPIRED
        ]

    async def test_a_retried_teardown_leaves_exactly_one_tombstone(self) -> None:
        """The second pass is a no-op on the record, not a second copy.

        Re-issuing a project delete is the sanctioned recovery, so it has to
        be free: the tombstone insert conflicts on the entity rather than on
        the row id, which makes a repeat write nothing at all instead of a
        duplicate-key error on the one table whose job is to still be there.
        """
        prepared = await _removal_pass(PlanDeleteOutcome(deleted=True))

        await prepared.run()
        await prepared.run()

        found = await prepared.backend.deleted_entities.query(
            DeletedEntityFilterSpec(entity_id=NotBlankStr(str(prepared.plan.id)))
        )
        assert len(found) == 1
