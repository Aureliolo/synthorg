"""Unit tests for the plan-approval resume dispatch branch."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.controllers._plan_review_resume import try_plan_review_resume
from synthorg.api.lifecycle_helpers.plan_review_wiring import PLAN_METADATA_KEY
from synthorg.api.state import AppState
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalSource, ApprovalStatus
from synthorg.core.approval import ApprovalItem
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
from synthorg.engine.decomposition.models import (
    DecompositionPlan,
    DecompositionResult,
    SubtaskDefinition,
)
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.registry import AgentRegistryService
from tests._shared import as_uuid, make_app_state, mock_of, sid
from tests._shared.scripted_provider import make_e2e_identity

pytestmark = pytest.mark.unit

#: Configured ``mock_of`` instance, typed loosely so the ``unittest.mock``
#: assertion API (``assert_awaited_once`` / ``await_args``) type-checks.
_Configured = Any  # type: ignore[explicit-any]

_NOW = datetime(2026, 7, 7, 12, 0, tzinfo=UTC)


def _task(label: str, *, status: TaskStatus = TaskStatus.ASSIGNED) -> Task:
    return Task(
        id=as_uuid(label),
        title=f"Task {label}",
        description=f"Description for {label}",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="proj-1",
        created_by="manager",
        assigned_to=str(as_uuid("agent-1")),
        status=status,
    )


def _plan(parent_label: str) -> DecompositionResult:
    """Build a two-subtask decomposition parented at *parent_label*."""
    sub_ids = (str(as_uuid("sub-1")), str(as_uuid("sub-2")))
    subtasks = tuple(
        SubtaskDefinition(
            id=NotBlankStr(sub_id),
            title=NotBlankStr(f"Subtask {n}"),
            description=NotBlankStr(f"Do part {n}"),
        )
        for n, sub_id in enumerate(sub_ids)
    )
    plan = DecompositionPlan(
        parent_task_id=NotBlankStr(str(as_uuid(parent_label))),
        subtasks=subtasks,
        task_structure=TaskStructure.PARALLEL,
        coordination_topology=CoordinationTopology.CENTRALIZED,
    )
    created = tuple(_task(f"sub-{n + 1}") for n in range(len(sub_ids)))
    return DecompositionResult(plan=plan, created_tasks=created)


def _approval(
    approval_id: str,
    *,
    source: ApprovalSource = ApprovalSource.PLAN_REVIEW,
    task_id: str | None,
    plan_json: str | None,
) -> ApprovalItem:
    metadata: dict[str, str] = {}
    if plan_json is not None:
        metadata[PLAN_METADATA_KEY] = plan_json
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


async def _seed(
    *,
    source: ApprovalSource = ApprovalSource.PLAN_REVIEW,
    task: Task | None,
    plan: DecompositionResult | None,
    coordinator_error: Exception | None = None,
) -> tuple[AppState, _Configured, _Configured]:
    store = ApprovalStore()
    await store.add(
        _approval(
            "appr-1",
            source=source,
            task_id=str(task.id) if task is not None else None,
            plan_json=plan.model_dump_json() if plan is not None else None,
        )
    )
    coordinator = mock_of[MultiAgentCoordinator](
        coordinate=AsyncMock(
            side_effect=coordinator_error,
            return_value=None if coordinator_error else object(),
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
    )
    return state, coordinator, engine


class TestPlanReviewResume:
    async def test_non_plan_source_is_inert(self) -> None:
        parent = _task("parent-1")
        state, coordinator, _ = await _seed(
            source=ApprovalSource.REVIEW_GATE,
            task=parent,
            plan=_plan("parent-1"),
        )
        handled = await try_plan_review_resume(
            state, sid("appr-1"), approved=True, decided_by="admin"
        )
        assert handled is False
        coordinator.coordinate.assert_not_called()

    async def test_approve_dispatches_exact_plan(self) -> None:
        parent = _task("parent-1")
        plan = _plan("parent-1")
        state, coordinator, _ = await _seed(task=parent, plan=plan)
        handled = await try_plan_review_resume(
            state, sid("appr-1"), approved=True, decided_by="admin"
        )
        assert handled is True
        coordinator.coordinate.assert_awaited_once()
        context = coordinator.coordinate.await_args.args[0]
        precomputed = coordinator.coordinate.await_args.kwargs["precomputed_plan"]
        assert context.task.id == parent.id
        # The exact parked plan is dispatched verbatim (no re-decomposition).
        assert precomputed == plan

    async def test_reject_cancels_task_and_skips_dispatch(self) -> None:
        parent = _task("parent-1")
        state, coordinator, engine = await _seed(task=parent, plan=_plan("parent-1"))
        handled = await try_plan_review_resume(
            state, sid("appr-1"), approved=False, decided_by="admin"
        )
        assert handled is True
        coordinator.coordinate.assert_not_called()
        engine.transition_task.assert_awaited_once()
        call = engine.transition_task.await_args
        assert call.args[0] == str(parent.id)
        assert call.args[1] is TaskStatus.CANCELLED

    async def test_missing_task_owned_but_noop(self) -> None:
        # The parked plan's parent task no longer exists: the flow owns the
        # decision (returns True) but cannot dispatch.
        state, coordinator, _ = await _seed(task=None, plan=_plan("parent-1"))
        # Re-seed the approval with a real task_id so ownership routing holds
        # even though get_task now yields None.
        store = ApprovalStore()
        await store.add(
            _approval(
                "appr-1",
                task_id=str(as_uuid("parent-1")),
                plan_json=_plan("parent-1").model_dump_json(),
            )
        )
        state = make_app_state(
            approval_store=store,
            coordinator=coordinator,
            task_engine=mock_of[TaskEngine](get_task=AsyncMock(return_value=None)),
            agent_registry=mock_of[AgentRegistryService](
                list_active=AsyncMock(return_value=())
            ),
        )
        handled = await try_plan_review_resume(
            state, sid("appr-1"), approved=True, decided_by="admin"
        )
        assert handled is True
        coordinator.coordinate.assert_not_called()

    async def test_dispatch_failure_still_owns_decision(self) -> None:
        # A dispatch failure must not 5xx the approval-decision request: the
        # flow still owns the decision (True) after logging.
        parent = _task("parent-1")
        state, coordinator, _ = await _seed(
            task=parent,
            plan=_plan("parent-1"),
            coordinator_error=RuntimeError("boom"),
        )
        handled = await try_plan_review_resume(
            state, sid("appr-1"), approved=True, decided_by="admin"
        )
        assert handled is True
        coordinator.coordinate.assert_awaited_once()
