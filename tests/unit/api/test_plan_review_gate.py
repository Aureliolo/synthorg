"""Unit tests for ``PlanReviewApprovalGate`` durable-plan persistence."""

from typing import override

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.lifecycle_helpers.plan_review_wiring import (
    PLAN_ID_METADATA_KEY,
    PlanReviewApprovalGate,
)
from synthorg.core.persistence_errors import QueryError
from synthorg.core.plan import Plan
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.models import (
    DecompositionPlan,
    DecompositionResult,
    SubtaskDefinition,
)
from synthorg.engine.pipeline.models import WorkItem, WorkSource
from tests._shared import FakeClock, as_uuid, sid
from tests.unit.api.fakes import FakePlanRepository

pytestmark = pytest.mark.unit


class _FailingPlanRepository(FakePlanRepository):
    """Plan repo whose ``create`` always fails, to exercise the fail path."""

    @override
    async def create(self, plan: Plan) -> None:
        msg = "boom"
        raise QueryError(msg)


def _result_task(subtask_id: str) -> Task:
    return Task(
        id=as_uuid(subtask_id),
        title=f"Subtask {subtask_id}",
        description=f"Description for {subtask_id}",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="beachhead",
        created_by="ceo",
    )


def _decomposition() -> DecompositionResult:
    plan = DecompositionPlan(
        parent_task_id=sid("root"),
        subtasks=(
            SubtaskDefinition(id=sid("sub-1"), title="A", description="Board grid"),
            SubtaskDefinition(
                id=sid("sub-2"),
                title="B",
                description="Movement",
                dependencies=(sid("sub-1"),),
            ),
        ),
    )
    return DecompositionResult(
        plan=plan,
        created_tasks=(_result_task("sub-1"), _result_task("sub-2")),
    )


def _work_item() -> WorkItem:
    return WorkItem(
        origin_adapter_id=NotBlankStr("charter-dispatch"),
        source=WorkSource.OBJECTIVE,
        title=NotBlankStr("Ship the game"),
        raw_intent=NotBlankStr("Build a playable single-player game"),
        project=NotBlankStr("beachhead"),
        requested_by=NotBlankStr("ceo"),
        plan_required=True,
    )


class TestPlanReviewApprovalGate:
    async def test_persists_durable_plan_and_references_id(self) -> None:
        store = ApprovalStore()
        plans = FakePlanRepository()
        gate = PlanReviewApprovalGate(
            approval_store=store,
            plans=plans,
            clock=FakeClock(),
        )
        task = _result_task("root")

        handoff = await gate.request_plan_approval(
            work_item=_work_item(),
            task=task,
            plan=_decomposition(),
        )

        assert handoff.subtask_count == 2

        persisted = await plans.list_items()
        assert len(persisted) == 1
        durable = persisted[0]
        assert durable.status is PlanStatus.PENDING_REVIEW
        assert durable.parent_task_id == str(task.id)
        assert len(durable.items) == 2

        parked = await store.list_items()
        assert len(parked) == 1
        metadata = parked[0].metadata
        assert metadata[PLAN_ID_METADATA_KEY] == str(durable.id)

    async def test_persistence_failure_parks_no_approval(self) -> None:
        store = ApprovalStore()
        gate = PlanReviewApprovalGate(
            approval_store=store,
            plans=_FailingPlanRepository(),
            clock=FakeClock(),
        )

        with pytest.raises(QueryError):
            await gate.request_plan_approval(
                work_item=_work_item(),
                task=_result_task("root"),
                plan=_decomposition(),
            )

        # The plan is persisted before the approval is parked, so a persistence
        # failure must leave no dangling approval behind.
        assert await store.list_items() == ()
