"""Unit tests for the stalled-initiative decision flow.

The decision exists because an initiative ran out of automatic road. An answer
that changed nothing would be the same defect one level up, so both answers are
asserted on what they actually did.
"""

from datetime import UTC, datetime
from uuid import UUID

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.controllers._approval_initiative_stall import (
    try_initiative_stall_resume,
)
from synthorg.api.services.plan_service_factory import build_plan_service
from synthorg.api.state import AppState
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.approval.initiative_stall import INITIATIVE_STALL_ACTION_TYPE
from synthorg.core.approval import ApprovalItem
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.completion import StallReason
from synthorg.engine.initiative.rollup import ProjectRollupService
from synthorg.engine.initiative.stall_escalation import PLAN_ID_METADATA_KEY
from synthorg.engine.state import EngineStateSlice
from tests._shared import (
    FakeClock,
    RecordingReplanTrigger,
    as_uuid,
    make_app_state,
    sid,
)
from tests.unit.api.fakes_backend import FakePersistenceBackend

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 8, 19, 9, 0, tzinfo=UTC)
_DECIDER = "operator-1"
_PLAN_ID = "plan-1"
_PROJECT = "proj-1"
_PARENT = sid("parent-1")
_ITEM_A = sid("item-a")
_APPROVAL = "approval-1"


def _plan() -> Plan:
    return Plan(
        id=as_uuid(_PLAN_ID),
        project=NotBlankStr(sid(_PROJECT)),
        project_name=NotBlankStr("Platform"),
        objective_id=NotBlankStr("obj-1"),
        objective_title=NotBlankStr("Ship the thing"),
        parent_task_id=NotBlankStr(_PARENT),
        created_at=_NOW,
        updated_at=_NOW,
        items=(
            PlanItem(
                id=NotBlankStr(_ITEM_A),
                title=NotBlankStr("Build it"),
                description=NotBlankStr("Build the thing"),
                acceptance_criteria=(NotBlankStr("it is done"),),
                expected_artifacts=(NotBlankStr("src/thing.py"),),
            ),
        ),
        status=PlanStatus.EXECUTING,
    )


def _task(status: TaskStatus) -> Task:
    return Task(
        id=UUID(_ITEM_A),
        title=NotBlankStr("Build it"),
        description=NotBlankStr("Build the thing"),
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project=sid(_PROJECT),
        plan_id=as_uuid(_PLAN_ID),
        plan_item_id=UUID(_ITEM_A),
        created_by="manager",
        assigned_to=sid("agent-1"),
        status=status,
    )


def _decision(*, action_type: str = INITIATIVE_STALL_ACTION_TYPE) -> ApprovalItem:
    return ApprovalItem(
        id=as_uuid(_APPROVAL),
        action_type=NotBlankStr(action_type),
        title=NotBlankStr("Initiative stopped: Ship the thing"),
        description=NotBlankStr("Every outstanding item is dead"),
        requested_by=NotBlankStr("initiative-rollup"),
        risk_level=ApprovalRiskLevel.HIGH,
        status=ApprovalStatus.PENDING,
        created_at=_NOW,
        task_id=NotBlankStr(_PARENT),
        metadata={PLAN_ID_METADATA_KEY: sid(_PLAN_ID)},
    )


async def _seed(
    *,
    task_status: TaskStatus = TaskStatus.FAILED,
    action_type: str = INITIATIVE_STALL_ACTION_TYPE,
    with_plan: bool = True,
    with_trigger: bool = True,
) -> tuple[AppState, FakePersistenceBackend, RecordingReplanTrigger | None]:
    """Stand up an app state around one stalled initiative and its decision.

    Returns:
        The app state, its persistence backend, and the trigger when wired.
    """
    backend = FakePersistenceBackend()
    if with_plan:
        await backend.plans.save(_plan())
        await backend.tasks.save(_task(task_status))
    store = ApprovalStore()
    await store.add(_decision(action_type=action_type))
    clock = FakeClock()
    trigger = RecordingReplanTrigger() if with_trigger else None
    rollup = ProjectRollupService(
        persistence=backend,
        plan_status_writer=build_plan_service(backend, clock=clock),
        clock=clock,
        replan_trigger=trigger,
    )
    app_state = make_app_state(
        persistence=backend,
        approval_store=store,
        clock=clock,
        slices={EngineStateSlice: {"project_rollup_service": rollup}},
    )
    return app_state, backend, trigger


class TestOwnership:
    async def test_another_approval_falls_through(self) -> None:
        app_state, _, _ = await _seed(action_type="org:hire")

        assert (
            await try_initiative_stall_resume(
                app_state, sid(_APPROVAL), approved=True, decided_by=_DECIDER
            )
            is False
        )

    async def test_a_missing_plan_is_owned_and_finished(self) -> None:
        """Falling through would let the review gate read it as a task review."""
        app_state, _, trigger = await _seed(with_plan=False)

        owned = await try_initiative_stall_resume(
            app_state, sid(_APPROVAL), approved=True, decided_by=_DECIDER
        )

        assert owned is True
        assert trigger is not None
        assert trigger.granted == []


class TestApproved:
    async def test_it_replans_on_the_operators_authority(self) -> None:
        app_state, _, trigger = await _seed()

        await try_initiative_stall_resume(
            app_state, sid(_APPROVAL), approved=True, decided_by=_DECIDER
        )

        assert trigger is not None
        assert trigger.granted == [(sid(_PLAN_ID), StallReason.ALL_FAILED, _DECIDER)]

    async def test_a_recovered_plan_is_not_replanned(self) -> None:
        """The answer may be hours old; a hand-authored replan must survive it."""
        app_state, _, trigger = await _seed(task_status=TaskStatus.IN_PROGRESS)

        await try_initiative_stall_resume(
            app_state, sid(_APPROVAL), approved=True, decided_by=_DECIDER
        )

        assert trigger is not None
        assert trigger.granted == []

    async def test_no_trigger_fails_the_plan_rather_than_reporting_nothing(
        self,
    ) -> None:
        app_state, backend, _ = await _seed(with_trigger=False)

        await try_initiative_stall_resume(
            app_state, sid(_APPROVAL), approved=True, decided_by=_DECIDER
        )

        plan = await backend.plans.get(NotBlankStr(sid(_PLAN_ID)))
        assert plan is not None
        assert plan.status is PlanStatus.FAILED


class TestRejected:
    async def test_it_fails_the_plan_with_the_stall_reason(self) -> None:
        app_state, backend, trigger = await _seed()

        await try_initiative_stall_resume(
            app_state, sid(_APPROVAL), approved=False, decided_by=_DECIDER
        )

        plan = await backend.plans.get(NotBlankStr(sid(_PLAN_ID)))
        assert plan is not None
        assert plan.status is PlanStatus.FAILED
        assert plan.failure_reason is not None
        assert "all_failed" in plan.failure_reason
        assert trigger is not None
        assert trigger.granted == []
