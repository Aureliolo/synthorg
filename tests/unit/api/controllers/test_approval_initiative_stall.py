"""Unit tests for the stalled-initiative decision flow.

The decision exists because an initiative ran out of automatic road. An answer
that changed nothing would be the same defect one level up, so both answers are
asserted on what they actually did.
"""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.controllers._approval_initiative_stall import (
    try_initiative_stall_resume,
)
from synthorg.api.services.plan_service_factory import build_plan_service
from synthorg.api.state import AppState
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.approval.initiative_stall import (
    DISPOSITION_METADATA_KEY,
    ESCALATION_ACTOR,
    INITIATIVE_STALL_ACTION_TYPE,
    PLAN_ID_METADATA_KEY,
    REASON_METADATA_KEY,
)
from synthorg.core.actor_context import ActorIdentity, ActorKind, actor_scope
from synthorg.core.approval import ApprovalItem
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.completion import ReplanDisposition, StallReason
from synthorg.engine.initiative.rollup import ProjectRollupService
from synthorg.engine.state import EngineStateSlice
from tests._shared import (
    FakeClock,
    RecordingReplanTrigger,
    as_pk,
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


@pytest.fixture(autouse=True)
def _a_person_is_deciding() -> Iterator[None]:
    """Bind a human actor, which is what every decision here is taken by.

    The grant lifts the operator's replan cap and master switch on the sole
    justification that a person asked, so the flow reads the actor rather than
    the decider's name (which anything can set). Every test that is about
    something else still needs one bound, or it is testing the refusal.
    """
    with actor_scope(
        ActorIdentity(actor_id=NotBlankStr(_DECIDER), kind=ActorKind.HUMAN)
    ):
        yield


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
        id=as_pk(_ITEM_A),
        title=NotBlankStr("Build it"),
        description=NotBlankStr("Build the thing"),
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project=sid(_PROJECT),
        plan_id=as_uuid(_PLAN_ID),
        plan_item_id=as_pk(_ITEM_A),
        created_by="manager",
        assigned_to=sid("agent-1"),
        status=status,
    )


def _decision(
    *,
    action_type: str = INITIATIVE_STALL_ACTION_TYPE,
    requested_by: str = ESCALATION_ACTOR,
    status: ApprovalStatus = ApprovalStatus.APPROVED,
    reason: StallReason = StallReason.ALL_FAILED,
) -> ApprovalItem:
    """Build the decision the flow reads back.

    Returns:
        The item, decided by default because that is the state the flow is
        called in: the caller has just written the answer.
    """
    decided = None if status is ApprovalStatus.PENDING else _NOW
    return ApprovalItem(
        id=as_uuid(_APPROVAL),
        action_type=NotBlankStr(action_type),
        title=NotBlankStr("Initiative stopped: Ship the thing"),
        description=NotBlankStr("Every outstanding item is dead"),
        requested_by=NotBlankStr(requested_by),
        risk_level=ApprovalRiskLevel.HIGH,
        status=status,
        created_at=_NOW,
        decided_at=decided,
        decided_by=None if decided is None else NotBlankStr(_DECIDER),
        decision_reason=(
            NotBlankStr("the operator ended it")
            if status is ApprovalStatus.REJECTED
            else None
        ),
        task_id=NotBlankStr(_PARENT),
        metadata={
            PLAN_ID_METADATA_KEY: sid(_PLAN_ID),
            REASON_METADATA_KEY: reason.value,
            DISPOSITION_METADATA_KEY: ReplanDisposition.BUDGET_EXHAUSTED.value,
        },
    )


async def _seed(
    *,
    task_status: TaskStatus = TaskStatus.FAILED,
    plan_status: PlanStatus = PlanStatus.EXECUTING,
    decision: ApprovalItem | None = None,
    with_plan: bool = True,
    with_trigger: bool = True,
    with_decision: bool = True,
) -> tuple[AppState, FakePersistenceBackend, RecordingReplanTrigger | None]:
    """Stand up an app state around one stalled initiative and its decision.

    Returns:
        The app state, its persistence backend, and the trigger when wired.
    """
    backend = FakePersistenceBackend()
    if with_plan:
        await backend.plans.save(_plan().model_copy(update={"status": plan_status}))
        await backend.tasks.save(_task(task_status))
    store = ApprovalStore()
    if with_decision:
        await store.add(decision if decision is not None else _decision())
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
        app_state, _, _ = await _seed(decision=_decision(action_type="org:hire"))

        assert (
            await try_initiative_stall_resume(
                app_state, sid(_APPROVAL), approved=True, decided_by=_DECIDER
            )
            is False
        )

    async def test_an_approval_that_is_gone_falls_through(self) -> None:
        """Nothing to own: the re-read found no row at all."""
        app_state, _, _ = await _seed(with_decision=False)

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


class TestProvenance:
    """The action type says what a decision asks, not who asked it."""

    async def test_an_item_this_organisation_did_not_raise_is_refused(self) -> None:
        """``POST /approvals`` copies an action type and metadata verbatim.

        Acting on one would let anything holding write access aim a plan
        failure, or a budget-lifting replan, at any initiative it can name. It
        is claimed rather than declined, because an unclaimed item carrying the
        objective task's id reaches the review-gate flow next and is read there
        as a completion review: declining would hand the forger a path one flow
        further down.
        """
        app_state, backend, trigger = await _seed(
            decision=_decision(requested_by="pair-programmer-3")
        )

        owned = await try_initiative_stall_resume(
            app_state, sid(_APPROVAL), approved=False, decided_by=_DECIDER
        )

        assert owned is True
        assert trigger is not None
        assert trigger.granted == []
        plan = await backend.plans.get(NotBlankStr(sid(_PLAN_ID)))
        assert plan is not None
        assert plan.status is PlanStatus.EXECUTING

    async def test_an_answer_the_row_does_not_carry_is_refused(self) -> None:
        """The two can only disagree if something replayed or rewrote it."""
        app_state, backend, trigger = await _seed(
            decision=_decision(status=ApprovalStatus.REJECTED)
        )

        owned = await try_initiative_stall_resume(
            app_state, sid(_APPROVAL), approved=True, decided_by=_DECIDER
        )

        assert owned is True
        assert trigger is not None
        assert trigger.granted == []
        plan = await backend.plans.get(NotBlankStr(sid(_PLAN_ID)))
        assert plan is not None
        assert plan.status is PlanStatus.EXECUTING


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

    async def test_a_non_human_decision_gets_the_unasked_authority(self) -> None:
        """Only a person's ask lifts the cap, so nothing else may grant.

        The org's own budget applies instead, exactly as it would with nobody
        asking at all.
        """
        app_state, _, trigger = await _seed()

        with actor_scope(
            ActorIdentity(actor_id=NotBlankStr("agent-7"), kind=ActorKind.AGENT)
        ):
            await try_initiative_stall_resume(
                app_state, sid(_APPROVAL), approved=True, decided_by="agent-7"
            )

        assert trigger is not None
        assert trigger.granted == []
        assert trigger.fired == [(sid(_PLAN_ID), StallReason.ALL_FAILED)]


class TestTailStageVerdicts:
    """A stall no derivation over items can see still has to be answerable."""

    async def test_an_integration_failure_is_confirmed_by_the_stage(self) -> None:
        """Every item IS done here, so deriving over items answers "recovered".

        Re-derived that way, both answers no-op: the item is consumed, the
        plan stays INTEGRATING for ever, and the next pass raises a fresh
        decision because only a PENDING one counts as open.
        """
        app_state, _, trigger = await _seed(
            task_status=TaskStatus.COMPLETED,
            plan_status=PlanStatus.INTEGRATING,
            decision=_decision(reason=StallReason.INTEGRATION_FAILED),
        )

        await try_initiative_stall_resume(
            app_state, sid(_APPROVAL), approved=True, decided_by=_DECIDER
        )

        assert trigger is not None
        assert trigger.granted == [
            (sid(_PLAN_ID), StallReason.INTEGRATION_FAILED, _DECIDER)
        ]

    async def test_an_unmet_evaluation_is_confirmed_by_the_stage(self) -> None:
        app_state, backend, _ = await _seed(
            task_status=TaskStatus.COMPLETED,
            plan_status=PlanStatus.EVALUATING,
            decision=_decision(
                reason=StallReason.EVALUATION_UNMET,
                status=ApprovalStatus.REJECTED,
            ),
        )

        await try_initiative_stall_resume(
            app_state, sid(_APPROVAL), approved=False, decided_by=_DECIDER
        )

        plan = await backend.plans.get(NotBlankStr(sid(_PLAN_ID)))
        assert plan is not None
        assert plan.status is PlanStatus.FAILED
        assert plan.failure_reason is not None
        assert "evaluation_unmet" in plan.failure_reason

    async def test_a_plan_that_left_the_stage_is_no_longer_stalled(self) -> None:
        """The stage produced the verdict, so leaving it is the recovery."""
        app_state, backend, trigger = await _seed(
            task_status=TaskStatus.COMPLETED,
            plan_status=PlanStatus.EVALUATING,
            decision=_decision(reason=StallReason.INTEGRATION_FAILED),
        )

        await try_initiative_stall_resume(
            app_state, sid(_APPROVAL), approved=True, decided_by=_DECIDER
        )

        assert trigger is not None
        assert trigger.granted == []
        plan = await backend.plans.get(NotBlankStr(sid(_PLAN_ID)))
        assert plan is not None
        assert plan.status is PlanStatus.EVALUATING


class TestRejected:
    async def test_it_fails_the_plan_with_the_stall_reason(self) -> None:
        app_state, backend, trigger = await _seed(
            decision=_decision(status=ApprovalStatus.REJECTED)
        )

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
