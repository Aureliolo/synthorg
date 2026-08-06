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
from synthorg.core.task_enums import Priority, TaskStructure, TaskType
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


class _UpdateFailingPlanRepository(FakePlanRepository):
    """Plan repo whose ``update`` always fails, to exercise the double-fault."""

    @override
    async def update(self, plan: Plan, *, expected_version: int | None = None) -> None:
        msg = "update boom"
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
            SubtaskDefinition(
                id=sid("sub-1"),
                title="A",
                description="Board grid",
                acceptance_criteria=(NotBlankStr("board renders"),),
                expected_artifacts=(NotBlankStr("src/board.py"),),
            ),
            SubtaskDefinition(
                id=sid("sub-2"),
                title="B",
                description="Movement",
                dependencies=(sid("sub-1"),),
                acceptance_criteria=(NotBlankStr("pieces move"),),
                expected_artifacts=(NotBlankStr("src/movement.py"),),
            ),
        ),
        task_structure=TaskStructure.SEQUENTIAL,
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
    async def test_open_plan_persists_planning_shell(self) -> None:
        plans = FakePlanRepository()
        gate = PlanReviewApprovalGate(
            approval_store=ApprovalStore(),
            plans=plans,
            clock=FakeClock(),
        )
        task = _result_task("root")

        plan_id = await gate.open_plan(work_item=_work_item(), task=task)

        # A first-class plan exists at greenlight, PLANNING, with no items yet.
        shell = await plans.get(NotBlankStr(str(plan_id)))
        assert shell is not None
        assert shell.status is PlanStatus.PLANNING
        assert shell.items == ()
        assert shell.parent_task_id == str(task.id)

    async def test_fills_shell_and_references_id(self) -> None:
        store = ApprovalStore()
        plans = FakePlanRepository()
        gate = PlanReviewApprovalGate(
            approval_store=store,
            plans=plans,
            clock=FakeClock(),
        )
        task = _result_task("root")
        work_item = _work_item()

        plan_id = await gate.open_plan(work_item=work_item, task=task)
        handoff = await gate.request_plan_approval(
            plan_id=plan_id,
            work_item=work_item,
            task=task,
            plan=_decomposition(),
        )

        assert handoff.subtask_count == 2
        assert handoff.approval_id is not None
        assert handoff.plan_id == str(plan_id)

        # The shell was filled in place (same id), not re-created.
        persisted = await plans.list_items()
        assert len(persisted) == 1
        durable = persisted[0]
        assert durable.id == plan_id
        assert durable.status is PlanStatus.PENDING_REVIEW
        assert durable.parent_task_id == str(task.id)
        assert len(durable.items) == 2

        parked = await store.list_items()
        assert len(parked) == 1
        assert parked[0].metadata[PLAN_ID_METADATA_KEY] == str(durable.id)

    async def test_fail_plan_marks_failed_with_reason(self) -> None:
        plans = FakePlanRepository()
        gate = PlanReviewApprovalGate(
            approval_store=ApprovalStore(),
            plans=plans,
            clock=FakeClock(),
        )
        task = _result_task("root")
        plan_id = await gate.open_plan(work_item=_work_item(), task=task)

        await gate.fail_plan(plan_id=plan_id, reason="decompose boom")

        failed = await plans.get(NotBlankStr(str(plan_id)))
        assert failed is not None
        assert failed.status is PlanStatus.FAILED
        assert failed.failure_reason == "decompose boom"
        assert failed.items == ()

    async def test_open_plan_persistence_failure_raises(self) -> None:
        gate = PlanReviewApprovalGate(
            approval_store=ApprovalStore(),
            plans=_FailingPlanRepository(),
            clock=FakeClock(),
        )

        with pytest.raises(QueryError):
            await gate.open_plan(work_item=_work_item(), task=_result_task("root"))

    async def test_approval_write_failure_marks_plan_failed(self) -> None:
        # The plan is filled, then the approval write fails: rather than deleting
        # the now-first-class plan, it is marked FAILED so the failure stays
        # visible in Plan Review (a retry is a fresh run, not a resurrected plan).
        plans = FakePlanRepository()
        gate = PlanReviewApprovalGate(
            approval_store=_FailingApprovalStore(),
            plans=plans,
            clock=FakeClock(),
        )
        task = _result_task("root")
        work_item = _work_item()
        plan_id = await gate.open_plan(work_item=work_item, task=task)

        with pytest.raises(QueryError):
            await gate.request_plan_approval(
                plan_id=plan_id,
                work_item=work_item,
                task=task,
                plan=_decomposition(),
            )

        persisted = await plans.list_items()
        assert len(persisted) == 1
        assert persisted[0].status is PlanStatus.FAILED
        assert persisted[0].failure_reason == "approval-store write failed"

    async def test_fail_plan_write_failure_is_swallowed_not_raised(self) -> None:
        # The compensating FAILED write is the one on the failure path; if it
        # itself fails, fail_plan must NOT raise (that would reintroduce the 500
        # this whole change removes). The plan stays PLANNING and it is logged.
        plans = _UpdateFailingPlanRepository()
        gate = PlanReviewApprovalGate(
            approval_store=ApprovalStore(),
            plans=plans,
            clock=FakeClock(),
        )
        task = _result_task("root")
        plan_id = await gate.open_plan(work_item=_work_item(), task=task)

        # No exception escapes.
        await gate.fail_plan(plan_id=plan_id, reason="decompose boom")

        shell = await plans.get(NotBlankStr(str(plan_id)))
        assert shell is not None
        assert shell.status is PlanStatus.PLANNING

    async def test_fail_plan_is_idempotent_on_already_failed(self) -> None:
        plans = FakePlanRepository()
        gate = PlanReviewApprovalGate(
            approval_store=ApprovalStore(),
            plans=plans,
            clock=FakeClock(),
        )
        task = _result_task("root")
        plan_id = await gate.open_plan(work_item=_work_item(), task=task)

        await gate.fail_plan(plan_id=plan_id, reason="first")
        first = await plans.get(NotBlankStr(str(plan_id)))
        # A second compensation (e.g. the outer pipeline guard after the
        # approval-store path already failed it) is a no-op, not a re-write.
        await gate.fail_plan(plan_id=plan_id, reason="second")
        second = await plans.get(NotBlankStr(str(plan_id)))

        assert first is not None
        assert second is not None
        assert second.version == first.version
        assert second.failure_reason == "first"

    async def test_fail_plan_missing_shell_is_noop(self) -> None:
        plans = FakePlanRepository()
        gate = PlanReviewApprovalGate(
            approval_store=ApprovalStore(),
            plans=plans,
            clock=FakeClock(),
        )
        # No shell opened; fail_plan on an unknown id must not raise.
        await gate.fail_plan(plan_id=as_uuid("ghost"), reason="boom")
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
