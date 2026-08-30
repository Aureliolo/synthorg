"""Unit tests for the extend-workstream extension-ask decision flow.

Simpler than the stall flow it mirrors: a rejection never fails the plan, so
every test here asserts on the trigger it was, or was not, asked to grant or
consider, not on a plan-status transition.
"""

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from structlog.testing import capture_logs

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.controllers._approval_initiative_extension import (
    try_initiative_extension_resume,
)
from synthorg.api.services.plan_service_factory import build_plan_service
from synthorg.api.state import AppState
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.approval.initiative_extension import (
    EXTENSION_ESCALATION_ACTOR,
    INITIATIVE_EXTENSION_ACTION_TYPE,
    LEAF_ID_METADATA_KEY,
    PLAN_ID_METADATA_KEY,
    WORKSTREAM_ID_METADATA_KEY,
)
from synthorg.core.actor_context import ActorIdentity, ActorKind, actor_scope
from synthorg.core.approval import ApprovalItem
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.rollup import ProjectRollupService
from synthorg.engine.state import EngineStateSlice
from synthorg.observability.events.initiative import INITIATIVE_EXTENSION_NOT_GRANTED
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
_WORKSTREAM = sid("ws-1")
_LEAF = sid("leaf-1")
_APPROVAL = "approval-1"


@pytest.fixture(autouse=True)
def _a_person_is_deciding() -> Iterator[None]:
    """Bind a human actor, which every grant-path test needs."""
    with actor_scope(
        ActorIdentity(actor_id=NotBlankStr(_DECIDER), kind=ActorKind.HUMAN)
    ):
        yield


def _item(
    item_id: str, *, parent_id: str | None = None, unsplit_reason: str | None = None
) -> PlanItem:
    return PlanItem(
        id=item_id,
        parent_id=parent_id,
        title=NotBlankStr(f"Item {item_id[:4]}"),
        description=NotBlankStr("Do the thing"),
        acceptance_criteria=(NotBlankStr("it is done"),),
        expected_artifacts=(NotBlankStr("src/thing.py"),),
        satisfies=(NotBlankStr("the game is playable"),),
        unsplit_reason=NotBlankStr(unsplit_reason) if unsplit_reason else None,
    )


def _plan() -> Plan:
    return Plan(
        id=as_uuid(_PLAN_ID),
        project=NotBlankStr(sid(_PROJECT)),
        project_name=NotBlankStr("Platform"),
        objective_id=NotBlankStr("obj-1"),
        objective_title=NotBlankStr("Ship it"),
        parent_task_id=NotBlankStr(sid("parent-1")),
        created_at=_NOW,
        updated_at=_NOW,
        items=(
            _item(_WORKSTREAM),
            _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop"),
        ),
        status=PlanStatus.EXECUTING,
        objective_criteria=(NotBlankStr("the game is playable"),),
    )


def _decision(
    *,
    action_type: str = INITIATIVE_EXTENSION_ACTION_TYPE,
    requested_by: str = EXTENSION_ESCALATION_ACTOR,
    status: ApprovalStatus = ApprovalStatus.APPROVED,
    leaf_id: str = _LEAF,
    workstream_id: str | None = _WORKSTREAM,
) -> ApprovalItem:
    """Build the decision the flow reads back.

    Returns:
        The item, decided by default because that is the state the flow is
        called in: the caller has just written the answer.
    """
    decided = None if status is ApprovalStatus.PENDING else _NOW
    metadata = {
        PLAN_ID_METADATA_KEY: sid(_PLAN_ID),
        LEAF_ID_METADATA_KEY: leaf_id,
    }
    if workstream_id is not None:
        metadata[WORKSTREAM_ID_METADATA_KEY] = workstream_id
    return ApprovalItem(
        id=as_uuid(_APPROVAL),
        action_type=NotBlankStr(action_type),
        title=NotBlankStr("Extend workstream: Item ws-1"),
        description=NotBlankStr("The leaf may not have delivered its full scope"),
        requested_by=NotBlankStr(requested_by),
        risk_level=ApprovalRiskLevel.MEDIUM,
        status=status,
        created_at=_NOW,
        decided_at=decided,
        decided_by=None if decided is None else NotBlankStr(_DECIDER),
        decision_reason=(
            NotBlankStr("leave it as delivered")
            if status is ApprovalStatus.REJECTED
            else None
        ),
        task_id=NotBlankStr(sid("parent-1")),
        metadata=metadata,
    )


async def _seed(
    *,
    plan: Plan | None = None,
    decision: ApprovalItem | None = None,
    with_plan: bool = True,
    with_trigger: bool = True,
    with_decision: bool = True,
) -> tuple[AppState, FakePersistenceBackend, RecordingReplanTrigger | None]:
    """Stand up an app state around one workstream's extension ask and its decision.

    Returns:
        The app state, its persistence backend, and the trigger when wired.
    """
    backend = FakePersistenceBackend()
    if with_plan:
        await backend.plans.save(plan if plan is not None else _plan())
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
            await try_initiative_extension_resume(
                app_state, sid(_APPROVAL), approved=True, decided_by=_DECIDER
            )
            is False
        )

    async def test_an_approval_that_is_gone_falls_through(self) -> None:
        app_state, _, _ = await _seed(with_decision=False)

        assert (
            await try_initiative_extension_resume(
                app_state, sid(_APPROVAL), approved=True, decided_by=_DECIDER
            )
            is False
        )

    async def test_a_missing_plan_is_owned_and_finished(self) -> None:
        app_state, _, trigger = await _seed(with_plan=False)

        owned = await try_initiative_extension_resume(
            app_state, sid(_APPROVAL), approved=True, decided_by=_DECIDER
        )

        assert owned is True
        assert trigger is not None
        assert trigger.extensions_granted == []


class TestProvenance:
    """The action type says what a decision asks, not who asked it."""

    async def test_an_item_this_organisation_did_not_raise_is_refused(self) -> None:
        app_state, _, trigger = await _seed(
            decision=_decision(requested_by="pair-programmer-3")
        )

        owned = await try_initiative_extension_resume(
            app_state, sid(_APPROVAL), approved=True, decided_by=_DECIDER
        )

        assert owned is True
        assert trigger is not None
        assert trigger.extensions_granted == []

    async def test_an_answer_the_row_does_not_carry_is_refused(self) -> None:
        app_state, _, trigger = await _seed(
            decision=_decision(status=ApprovalStatus.REJECTED)
        )

        owned = await try_initiative_extension_resume(
            app_state, sid(_APPROVAL), approved=True, decided_by=_DECIDER
        )

        assert owned is True
        assert trigger is not None
        assert trigger.extensions_granted == []


class TestApproved:
    async def test_it_grants_the_extension_on_the_operators_authority(self) -> None:
        app_state, _, trigger = await _seed()

        await try_initiative_extension_resume(
            app_state, sid(_APPROVAL), approved=True, decided_by=_DECIDER
        )

        assert trigger is not None
        assert trigger.extensions_granted == [(sid(_PLAN_ID), _LEAF, _DECIDER)]

    async def test_a_leaf_already_extended_by_another_writer_is_a_no_op(self) -> None:
        """The leaf gained children before the decision was answered."""
        already_extended = _plan().model_copy(
            update={
                "items": (
                    _item(_WORKSTREAM),
                    _item(
                        _LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop"
                    ),
                    _item(sid("child-1"), parent_id=_LEAF),
                )
            }
        )
        app_state, _, trigger = await _seed(plan=already_extended)

        await try_initiative_extension_resume(
            app_state, sid(_APPROVAL), approved=True, decided_by=_DECIDER
        )

        assert trigger is not None
        assert trigger.extensions_granted == []

    async def test_no_trigger_reports_nothing_can_grant(self) -> None:
        app_state, backend, _ = await _seed(with_trigger=False)

        await try_initiative_extension_resume(
            app_state, sid(_APPROVAL), approved=True, decided_by=_DECIDER
        )

        plan = await backend.plans.get(NotBlankStr(sid(_PLAN_ID)))
        assert plan is not None
        assert plan.status is PlanStatus.EXECUTING

    async def test_a_workstream_that_no_longer_resolves_reports_and_grants_nothing(
        self,
    ) -> None:
        """C7: an unresolvable workstream is a logged refusal, not a silent one."""
        app_state, _, trigger = await _seed(
            decision=_decision(workstream_id=sid("gone"))
        )

        with capture_logs() as logs:
            await try_initiative_extension_resume(
                app_state, sid(_APPROVAL), approved=True, decided_by=_DECIDER
            )

        assert trigger is not None
        assert trigger.extensions_granted == []
        assert any(
            entry.get("event") == INITIATIVE_EXTENSION_NOT_GRANTED for entry in logs
        )

    async def test_a_non_human_decision_gets_the_unasked_authority(self) -> None:
        app_state, _, trigger = await _seed()

        with actor_scope(
            ActorIdentity(actor_id=NotBlankStr("agent-7"), kind=ActorKind.AGENT)
        ):
            await try_initiative_extension_resume(
                app_state, sid(_APPROVAL), approved=True, decided_by="agent-7"
            )

        assert trigger is not None
        assert trigger.extensions_granted == []
        assert trigger.extensions_considered == [(sid(_PLAN_ID), _LEAF)]


class TestRejected:
    async def test_it_does_not_grant_and_does_not_fail_the_plan(self) -> None:
        app_state, backend, trigger = await _seed(
            decision=_decision(status=ApprovalStatus.REJECTED)
        )

        await try_initiative_extension_resume(
            app_state, sid(_APPROVAL), approved=False, decided_by=_DECIDER
        )

        plan = await backend.plans.get(NotBlankStr(sid(_PLAN_ID)))
        assert plan is not None
        assert plan.status is PlanStatus.EXECUTING
        assert trigger is not None
        assert trigger.extensions_granted == []
