"""Unit tests for ``PlanReviewApprovalGate`` durable-plan persistence."""

from typing import override
from unittest.mock import AsyncMock, Mock

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.lifecycle_helpers.plan_review_wiring import (
    PLAN_ID_METADATA_KEY,
    PlanReviewApprovalGate,
    wire_plan_review_gate,
)
from synthorg.api.state import AppState
from synthorg.core.approval import ApprovalItem
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
from synthorg.engine.pipeline.protocol import WorkPipeline
from synthorg.settings.resolver import ConfigResolver
from tests._shared import FakeClock, as_uuid, make_app_state, mock_of, sid
from tests.unit.api.fakes import FakePlanRepository
from tests.unit.api.fakes_backend import FakePersistenceBackend

pytestmark = pytest.mark.unit


class _FailingPlanRepository(FakePlanRepository):
    """Plan repo whose ``create`` always fails, to exercise the fail path."""

    @override
    async def create(self, plan: Plan) -> None:
        msg = "boom"
        raise QueryError(msg)


class _FailingApprovalStore(ApprovalStore):
    """Approval store whose ``add`` always fails, to exercise compensation."""

    @override
    async def add(self, item: ApprovalItem) -> None:
        msg = "approval boom"
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

    async def test_approval_write_failure_deletes_orphan_plan(self) -> None:
        # The plan commits, then the approval write fails: without the approval
        # there is no route to ever decide the plan, so the created plan must be
        # compensated (deleted) rather than left as a permanent orphan.
        plans = FakePlanRepository()
        gate = PlanReviewApprovalGate(
            approval_store=_FailingApprovalStore(),
            plans=plans,
            clock=FakeClock(),
        )

        with pytest.raises(QueryError):
            await gate.request_plan_approval(
                work_item=_work_item(),
                task=_result_task("root"),
                plan=_decomposition(),
            )

        assert await plans.list_items() == ()


class TestWirePlanReviewGate:
    async def _make_state(
        self, *, required: bool, wire_deps: bool, wire_pipeline: bool = True
    ) -> tuple[AppState, Mock]:
        pipeline = mock_of[WorkPipeline](attach_plan_review_gate=Mock())
        resolver = mock_of[ConfigResolver](get_bool=AsyncMock(return_value=required))
        backend: FakePersistenceBackend | None = None
        store: ApprovalStore | None = None
        if wire_deps:
            backend = FakePersistenceBackend()
            await backend.connect()
            store = ApprovalStore()
        state = make_app_state(
            config_resolver=resolver,
            work_pipeline=pipeline if wire_pipeline else None,
            approval_store=store,
            persistence=backend,
        )
        return state, pipeline.attach_plan_review_gate

    async def test_attaches_when_required_and_wired(self) -> None:
        state, attach = await self._make_state(required=True, wire_deps=True)
        await wire_plan_review_gate(state)
        attach.assert_called_once()

    async def test_noop_when_not_required(self) -> None:
        state, attach = await self._make_state(required=False, wire_deps=True)
        await wire_plan_review_gate(state)
        attach.assert_not_called()

    async def test_noop_when_pipeline_absent(self) -> None:
        state, attach = await self._make_state(
            required=True, wire_deps=True, wire_pipeline=False
        )
        await wire_plan_review_gate(state)
        attach.assert_not_called()

    async def test_skips_when_required_but_deps_unwired(self) -> None:
        state, attach = await self._make_state(required=True, wire_deps=False)
        await wire_plan_review_gate(state)
        attach.assert_not_called()
