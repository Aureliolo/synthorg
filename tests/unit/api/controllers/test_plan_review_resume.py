"""Unit tests for the plan-approval resume dispatch branch."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.controllers._plan_review_resume import (
    _sync_plan_status,
    try_plan_review_resume,
)
from synthorg.api.lifecycle_helpers.plan_review_wiring import PLAN_ID_METADATA_KEY
from synthorg.api.state import AppState
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalSource, ApprovalStatus
from synthorg.core.approval import ApprovalItem
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.project import Project
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.task import Task
from synthorg.core.task_enums import (
    CoordinationTopology,
    Priority,
    TaskStatus,
    TaskStructure,
    TaskType,
)
from synthorg.core.types import NotBlankStr
from synthorg.engine.coordination.service import MultiAgentCoordinator
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.registry import AgentRegistryService
from tests._shared import as_uuid, make_app_state, mock_of, sid
from tests._shared.scripted_provider import make_e2e_identity
from tests.unit.api.fakes_backend import FakePersistenceBackend

pytestmark = pytest.mark.unit

#: Configured ``mock_of`` instance, typed loosely so the ``unittest.mock``
#: assertion API (``assert_awaited_once`` / ``await_args``) type-checks.
_Configured = Any  # type: ignore[explicit-any]

_NOW = datetime(2026, 7, 7, 12, 0, tzinfo=UTC)
_PLAN_ID = "plan-1"
_SUB_IDS = (str(as_uuid("sub-1")), str(as_uuid("sub-2")))


def _task(label: str, *, status: TaskStatus = TaskStatus.ASSIGNED) -> Task:
    return Task(
        id=as_uuid(label),
        title=f"Task {label}",
        description=f"Description for {label}",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project=sid("proj-1"),
        created_by="manager",
        assigned_to=str(as_uuid("agent-1")),
        status=status,
    )


def _durable_plan(parent_label: str) -> Plan:
    """Build a durable two-item plan parented at *parent_label*."""
    items = tuple(
        PlanItem(
            id=NotBlankStr(sub_id),
            title=NotBlankStr(f"Subtask {n}"),
            description=NotBlankStr(f"Do part {n}"),
            acceptance_criteria=(NotBlankStr(f"part {n} done"),),
            expected_artifacts=(NotBlankStr(f"src/part_{n}.py"),),
        )
        for n, sub_id in enumerate(_SUB_IDS)
    )
    return Plan(
        id=as_uuid(_PLAN_ID),
        project=NotBlankStr(sid("proj-1")),
        objective_id=NotBlankStr("obj-1"),
        objective_title=NotBlankStr("Ship the game"),
        parent_task_id=NotBlankStr(str(as_uuid(parent_label))),
        items=items,
        task_structure=TaskStructure.PARALLEL,
        coordination_topology=CoordinationTopology.CENTRALIZED,
        status=PlanStatus.PENDING_REVIEW,
        created_at=_NOW,
        updated_at=_NOW,
    )


def _approval(
    approval_id: str,
    *,
    source: ApprovalSource = ApprovalSource.PLAN_REVIEW,
    task_id: str | None,
    plan_id: str | None,
) -> ApprovalItem:
    metadata: dict[str, str] = {}
    if plan_id is not None:
        metadata[PLAN_ID_METADATA_KEY] = plan_id
    return ApprovalItem(
        id=as_uuid(approval_id),
        action_type=NotBlankStr("plan:approve"),
        title=NotBlankStr("Approve plan"),
        description=NotBlankStr("2 subtask(s)"),
        requested_by=NotBlankStr("user-1"),
        risk_level=ApprovalRiskLevel.MEDIUM,
        source=source,
        status=ApprovalStatus.PENDING,
        created_at=_NOW,
        task_id=NotBlankStr(task_id) if task_id is not None else None,
        metadata=metadata,
    )


_UNSET: Any = object()  # type: ignore[explicit-any]


async def _seed(  # noqa: PLR0913 -- test seam composing several independent knobs
    *,
    source: ApprovalSource = ApprovalSource.PLAN_REVIEW,
    task: Task | None,
    plan: Plan | None,
    coordinator_error: Exception | None = None,
    coordinator_missing: bool = False,
    approval_task_id: str | None = _UNSET,
    save_plan: bool = True,
    save_project: bool = True,
) -> tuple[AppState, _Configured, _Configured, FakePersistenceBackend]:
    resolved_task_id = (
        (str(task.id) if task is not None else None)
        if approval_task_id is _UNSET
        else approval_task_id
    )
    plan_id = str(plan.id) if plan is not None else None
    store = ApprovalStore()
    await store.add(
        _approval("appr-1", source=source, task_id=resolved_task_id, plan_id=plan_id)
    )
    backend = FakePersistenceBackend()
    await backend.connect()
    if plan is not None and save_plan:
        await backend.plans.save(plan)
    if save_project:
        # Dispatch follows a greenlight, so the project always exists by the
        # time a plan is approved. Without it the link write fails and the
        # dispatch is refused, which is a different scenario entirely.
        await backend.projects.save(
            Project(id=as_uuid("proj-1"), name=NotBlankStr("Initiative"))
        )
    coordinator = (
        None
        if coordinator_missing
        else mock_of[MultiAgentCoordinator](
            coordinate=AsyncMock(
                side_effect=coordinator_error,
                return_value=None if coordinator_error else object(),
            )
        )
    )
    engine = mock_of[TaskEngine](
        get_task=AsyncMock(return_value=task),
        transition_task=AsyncMock(return_value=None),
    )
    registry = mock_of[AgentRegistryService](
        list_active=AsyncMock(return_value=(make_e2e_identity(),))
    )
    state = make_app_state(
        approval_store=store,
        coordinator=coordinator,
        task_engine=engine,
        agent_registry=registry,
        persistence=backend,
    )
    return state, coordinator, engine, backend


class TestPlanReviewResume:
    async def test_non_plan_source_is_inert(self) -> None:
        state, coordinator, _, _ = await _seed(
            source=ApprovalSource.REVIEW_GATE,
            task=_task("parent-1"),
            plan=_durable_plan("parent-1"),
        )
        handled = await try_plan_review_resume(
            state, sid("appr-1"), approved=True, decided_by="admin"
        )
        assert handled is False
        coordinator.coordinate.assert_not_called()

    async def test_approve_dispatches_durable_plan(self) -> None:
        parent = _task("parent-1")
        state, coordinator, _, backend = await _seed(
            task=parent, plan=_durable_plan("parent-1")
        )
        handled = await try_plan_review_resume(
            state, sid("appr-1"), approved=True, decided_by="admin"
        )
        assert handled is True
        coordinator.coordinate.assert_awaited_once()
        context = coordinator.coordinate.await_args.args[0]
        precomputed = coordinator.coordinate.await_args.kwargs["precomputed_plan"]
        assert context.task.id == parent.id
        # The durable plan's items are rebuilt into the dispatched subtask tree.
        dispatched_ids = {s.id for s in precomputed.plan.subtasks}
        assert dispatched_ids == set(_SUB_IDS)
        # Each item's declared deliverable survives the rebuild onto its
        # subtask, so the dispatched task's fail-loud zero-artifact guard stays
        # armed rather than silently disarmed by a dropped mapping.
        assert {
            artifact
            for subtask in precomputed.plan.subtasks
            for artifact in subtask.expected_artifacts
        } == {NotBlankStr(f"src/part_{n}.py") for n in range(len(_SUB_IDS))}
        # Rebuilt child tasks are fresh CREATED work parented on the objective.
        assert all(t.status is TaskStatus.CREATED for t in precomputed.created_tasks)
        # Every dispatched task carries its plan linkage, so the rollup can
        # find a plan's tasks without re-deriving the id mapping.
        assert all(t.plan_id == as_uuid(_PLAN_ID) for t in precomputed.created_tasks)
        assert all(t.plan_item_id is not None for t in precomputed.created_tasks)
        # Approval dispatches the plan, so it moves past the decision into
        # execution rather than resting on the recorded verdict.
        stored = await backend.plans.get(NotBlankStr(str(as_uuid(_PLAN_ID))))
        assert stored is not None
        assert stored.status is PlanStatus.EXECUTING

    async def test_approve_links_and_activates_the_project(self) -> None:
        """The graph is connected before any dispatched task can run."""
        parent = _task("parent-1")
        state, _, _, backend = await _seed(task=parent, plan=_durable_plan("parent-1"))

        await try_plan_review_resume(
            state, sid("appr-1"), approved=True, decided_by="admin"
        )

        project = await backend.projects.get(NotBlankStr(sid("proj-1")))
        assert project is not None
        assert project.plan_id == as_uuid(_PLAN_ID)
        assert project.status is ProjectStatus.ACTIVE

    async def test_an_unlinkable_project_refuses_the_dispatch(self) -> None:
        """Dispatching against a project that never learned its plan is worse
        than not dispatching: the work runs, but its progress view reports no
        plan and its status can only advance by an illegal jump.
        """
        parent = _task("parent-1")
        state, coordinator, _, _ = await _seed(
            task=parent,
            plan=_durable_plan("parent-1"),
            save_project=False,
        )

        handled = await try_plan_review_resume(
            state, sid("appr-1"), approved=True, decided_by="admin"
        )

        assert handled is True
        coordinator.coordinate.assert_not_called()

    async def test_reject_cancels_task_and_marks_plan_rejected(self) -> None:
        parent = _task("parent-1")
        state, coordinator, engine, backend = await _seed(
            task=parent, plan=_durable_plan("parent-1")
        )
        handled = await try_plan_review_resume(
            state, sid("appr-1"), approved=False, decided_by="admin"
        )
        assert handled is True
        coordinator.coordinate.assert_not_called()
        engine.transition_task.assert_awaited_once()
        call = engine.transition_task.await_args
        assert call.args[0] == str(parent.id)
        assert call.args[1] is TaskStatus.CANCELLED
        stored = await backend.plans.get(NotBlankStr(str(as_uuid(_PLAN_ID))))
        assert stored is not None
        assert stored.status is PlanStatus.REJECTED

    async def test_sync_plan_status_aborts_on_raced_deletion(self) -> None:
        # The plan is present on the initial fetch but gone on the CAS re-read
        # (a delete raced the status sync). The loop must abort cleanly on the
        # not-found rather than spin its retries against the stale plan into a
        # misleading version-conflict error log.
        plan = _durable_plan("parent-1")
        state, _, _, backend = await _seed(task=_task("parent-1"), plan=plan)
        scripted_get = AsyncMock(side_effect=[plan, None])
        backend.plans.get = scripted_get  # type: ignore[method-assign]
        await _sync_plan_status(state, str(plan.id), PlanStatus.APPROVED)
        # Exactly two reads: the initial fetch plus one CAS read that saw the
        # deletion and aborted. A retry against the stale plan would read again.
        assert scripted_get.await_count == 2

    async def test_missing_task_marks_task_failed(self) -> None:
        # The approval references a task that no longer exists (get_task -> None):
        # the flow owns the decision but the parent task is marked FAILED so the
        # stuck plan surfaces rather than sitting silently in pre-approval status.
        state, coordinator, engine, _ = await _seed(
            task=None,
            plan=_durable_plan("parent-1"),
            approval_task_id=str(as_uuid("parent-1")),
        )
        handled = await try_plan_review_resume(
            state, sid("appr-1"), approved=True, decided_by="admin"
        )
        assert handled is True
        coordinator.coordinate.assert_not_called()
        engine.transition_task.assert_awaited_once()
        assert engine.transition_task.await_args.args[1] is TaskStatus.FAILED

    async def test_missing_plan_marks_task_failed(self) -> None:
        # The approval references a plan_id that is not persisted: the flow marks
        # the parent task FAILED rather than returning a silent no-op.
        parent = _task("parent-1")
        state, _, engine, _ = await _seed(
            task=parent, plan=_durable_plan("parent-1"), save_plan=False
        )
        handled = await try_plan_review_resume(
            state, sid("appr-1"), approved=True, decided_by="admin"
        )
        assert handled is True
        engine.transition_task.assert_awaited_once()
        assert engine.transition_task.await_args.args[1] is TaskStatus.FAILED

    async def test_missing_coordinator_marks_task_failed(self) -> None:
        parent = _task("parent-1")
        state, _, engine, _ = await _seed(
            task=parent, plan=_durable_plan("parent-1"), coordinator_missing=True
        )
        handled = await try_plan_review_resume(
            state, sid("appr-1"), approved=True, decided_by="admin"
        )
        assert handled is True
        engine.transition_task.assert_awaited_once()
        assert engine.transition_task.await_args.args[1] is TaskStatus.FAILED

    async def test_dispatch_failure_marks_task_failed_without_rolling_back(
        self,
    ) -> None:
        # A dispatch failure must not 5xx the approval-decision request: the flow
        # still owns the decision (True) and marks the task FAILED. The plan is
        # left EXECUTING rather than rolled back: the decision stands, the
        # failure belongs to the task, and it surfaces as a failed-item count on
        # the project rather than by rewinding the plan's lifecycle.
        parent = _task("parent-1")
        state, coordinator, engine, backend = await _seed(
            task=parent,
            plan=_durable_plan("parent-1"),
            coordinator_error=RuntimeError("boom"),
        )
        handled = await try_plan_review_resume(
            state, sid("appr-1"), approved=True, decided_by="admin"
        )
        assert handled is True
        coordinator.coordinate.assert_awaited_once()
        engine.transition_task.assert_awaited_once()
        assert engine.transition_task.await_args.args[1] is TaskStatus.FAILED
        stored = await backend.plans.get(NotBlankStr(str(as_uuid(_PLAN_ID))))
        assert stored is not None
        assert stored.status is PlanStatus.EXECUTING
