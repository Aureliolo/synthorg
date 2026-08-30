"""The just-in-time slice check the rollup runs while a plan is EXECUTING.

Meaningful only behind ``coordination.jit_slice_planning_enabled``: off, a
workstream whose only oversized leaf completed promotes the plan to
INTEGRATING exactly as it does today, with no new check running at all.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.services.plan_service_factory import build_plan_service
from synthorg.approval.enums import ApprovalStatus
from synthorg.approval.initiative_slice import INITIATIVE_SLICE_ACTION_TYPE
from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.project import Project
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.ports import DriveOutcome, PlanDriver
from synthorg.engine.initiative.rollup import ProjectRollupService
from synthorg.engine.initiative.slice_escalation import SliceEscalationService
from synthorg.engine.initiative.slice_state import SliceDisposition
from synthorg.settings.resolver import ConfigResolver
from tests._shared import (
    FakeClock,
    RecordingReplanTrigger,
    as_pk,
    as_uuid,
    mock_of,
    sid,
)
from tests.unit.api.fakes_backend import FakePersistenceBackend

pytestmark = pytest.mark.unit

_PLAN_ID = "plan-1"
_PROJECT = "proj-1"
_WORKSTREAM = sid("ws-1")
_LEAF = sid("leaf-1")


def _item(
    item_id: str,
    *,
    parent_id: str | None = None,
    unsplit_reason: str | None = None,
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


def _plan(*items: PlanItem, status: PlanStatus = PlanStatus.EXECUTING) -> Plan:
    now = datetime(2026, 7, 24, tzinfo=UTC)
    return Plan(
        id=as_uuid(_PLAN_ID),
        project=NotBlankStr(sid(_PROJECT)),
        project_name=NotBlankStr("Platform"),
        objective_id=NotBlankStr("obj-1"),
        objective_title=NotBlankStr("Ship it"),
        parent_task_id=NotBlankStr(sid("parent-1")),
        items=items,
        status=status,
        objective_criteria=(NotBlankStr("the game is playable"),),
        created_at=now,
        updated_at=now,
    )


def _task(item_id: str, status: TaskStatus) -> Task:
    return Task(
        id=as_pk(item_id),
        title="Child",
        description="Child work",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project=sid(_PROJECT),
        plan_id=as_uuid(_PLAN_ID),
        plan_item_id=as_pk(item_id),
        created_by="manager",
        assigned_to=sid("agent-1"),
        status=status,
    )


def _resolver(*, enabled: bool) -> ConfigResolver:
    resolver: ConfigResolver = mock_of[ConfigResolver](
        get_bool=AsyncMock(return_value=enabled)
    )
    return resolver


async def _seed(
    plan: Plan,
    *tasks: Task,
    config_resolver: ConfigResolver,
    replan_trigger: RecordingReplanTrigger | None = None,
    drive: PlanDriver | None = None,
    slice_escalation: SliceEscalationService | None = None,
) -> tuple[ProjectRollupService, FakePersistenceBackend]:
    backend = FakePersistenceBackend()
    await backend.plans.save(plan)
    await backend.projects.save(
        Project(
            id=as_uuid(_PROJECT),
            name=NotBlankStr("Initiative"),
            plan_id=as_uuid(_PLAN_ID),
        )
    )
    for task in tasks:
        await backend.tasks.save(task)
    clock = FakeClock()
    service = ProjectRollupService(
        persistence=backend,
        plan_status_writer=build_plan_service(backend, clock=clock),
        clock=clock,
        replan_trigger=replan_trigger,
        config_resolver=config_resolver,
    )
    if drive is not None or slice_escalation is not None:
        service.attach_tail(plan_driver=drive, slice_escalation=slice_escalation)
    return service, backend


def _oversized_leaf_plan() -> tuple[Plan, Task, Task]:
    """A workstream whose one leaf completed but was never atomic."""
    workstream = _item(_WORKSTREAM)
    leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
    plan = _plan(workstream, leaf)
    return (
        plan,
        _task(_WORKSTREAM, TaskStatus.COMPLETED),
        _task(_LEAF, TaskStatus.COMPLETED),
    )


class TestSliceMasterSwitch:
    """Off means today's behaviour, byte-for-byte."""

    async def test_off_promotes_to_integrating_as_today(self) -> None:
        plan, ws_task, leaf_task = _oversized_leaf_plan()
        trigger = RecordingReplanTrigger()
        service, backend = await _seed(
            plan,
            ws_task,
            leaf_task,
            config_resolver=_resolver(enabled=False),
            replan_trigger=trigger,
        )

        await service.recompute(plan.id)

        fresh = await backend.plans.get(str(plan.id))
        assert fresh is not None
        assert fresh.status is PlanStatus.INTEGRATING
        assert trigger.slices_considered == []

    async def test_no_trigger_wired_is_the_same_as_off(self) -> None:
        plan, ws_task, leaf_task = _oversized_leaf_plan()
        service, backend = await _seed(
            plan,
            ws_task,
            leaf_task,
            config_resolver=_resolver(enabled=True),
            replan_trigger=None,
        )

        await service.recompute(plan.id)

        fresh = await backend.plans.get(str(plan.id))
        assert fresh is not None
        assert fresh.status is PlanStatus.INTEGRATING


class TestSliceOnGraft:
    """On, an oversized-and-completed leaf is asked for a slice."""

    async def test_a_graft_holds_the_plan_at_executing_this_pass(self) -> None:
        plan, ws_task, leaf_task = _oversized_leaf_plan()
        trigger = RecordingReplanTrigger(slice_disposition=SliceDisposition.GRAFTED)
        service, backend = await _seed(
            plan,
            ws_task,
            leaf_task,
            config_resolver=_resolver(enabled=True),
            replan_trigger=trigger,
        )

        await service.recompute(plan.id)

        fresh = await backend.plans.get(str(plan.id))
        assert fresh is not None
        assert fresh.status is PlanStatus.EXECUTING
        assert trigger.slices_considered == [(str(plan.id), _LEAF)]

    async def test_the_driver_is_threaded_through_to_the_trigger(self) -> None:
        plan, ws_task, leaf_task = _oversized_leaf_plan()
        trigger = RecordingReplanTrigger(slice_disposition=SliceDisposition.GRAFTED)
        drive = AsyncMock(return_value=DriveOutcome.DRIVING)
        service, _ = await _seed(
            plan,
            ws_task,
            leaf_task,
            config_resolver=_resolver(enabled=True),
            replan_trigger=trigger,
            drive=drive,
        )

        await service.recompute(plan.id)

        assert trigger.slices_considered == [(str(plan.id), _LEAF)]
        assert trigger.slice_drives == [drive]


class TestSliceRefused:
    """A refusal with no automatic route left proceeds, not hangs, EXECUTING."""

    async def test_budget_exhausted_still_promotes_to_integrating(self) -> None:
        plan, ws_task, leaf_task = _oversized_leaf_plan()
        trigger = RecordingReplanTrigger(
            slice_disposition=SliceDisposition.BUDGET_EXHAUSTED
        )
        service, backend = await _seed(
            plan,
            ws_task,
            leaf_task,
            config_resolver=_resolver(enabled=True),
            replan_trigger=trigger,
        )

        await service.recompute(plan.id)

        fresh = await backend.plans.get(str(plan.id))
        assert fresh is not None
        assert fresh.status is PlanStatus.INTEGRATING
        assert trigger.slices_considered == [(str(plan.id), _LEAF)]

    async def test_disabled_still_promotes_to_integrating(self) -> None:
        plan, ws_task, leaf_task = _oversized_leaf_plan()
        trigger = RecordingReplanTrigger(slice_disposition=SliceDisposition.DISABLED)
        service, backend = await _seed(
            plan,
            ws_task,
            leaf_task,
            config_resolver=_resolver(enabled=True),
            replan_trigger=trigger,
        )

        await service.recompute(plan.id)

        fresh = await backend.plans.get(str(plan.id))
        assert fresh is not None
        assert fresh.status is PlanStatus.INTEGRATING


class TestSliceAsked:
    """The deterministic gate parks a decision, and only that decision holds."""

    async def test_asked_with_no_escalation_promotes_to_integrating(self) -> None:
        """No escalation attached: nothing can ask, so the plan is not held."""
        plan, ws_task, leaf_task = _oversized_leaf_plan()
        trigger = RecordingReplanTrigger(slice_disposition=SliceDisposition.ASKED)
        service, backend = await _seed(
            plan,
            ws_task,
            leaf_task,
            config_resolver=_resolver(enabled=True),
            replan_trigger=trigger,
        )

        await service.recompute(plan.id)

        fresh = await backend.plans.get(str(plan.id))
        assert fresh is not None
        assert fresh.status is PlanStatus.INTEGRATING

    async def test_asked_with_escalation_parks_a_decision_and_holds(self) -> None:
        plan, ws_task, leaf_task = _oversized_leaf_plan()
        trigger = RecordingReplanTrigger(slice_disposition=SliceDisposition.ASKED)
        store = ApprovalStore()
        escalation = SliceEscalationService(approvals=store, clock=FakeClock())
        service, backend = await _seed(
            plan,
            ws_task,
            leaf_task,
            config_resolver=_resolver(enabled=True),
            replan_trigger=trigger,
            slice_escalation=escalation,
        )

        await service.recompute(plan.id)

        fresh = await backend.plans.get(str(plan.id))
        assert fresh is not None
        assert fresh.status is PlanStatus.EXECUTING
        pending = await store.list_items(
            status=ApprovalStatus.PENDING,
            action_type=NotBlankStr(INITIATIVE_SLICE_ACTION_TYPE),
        )
        assert len(pending) == 1

    async def test_an_already_open_decision_is_never_asked_about_twice(self) -> None:
        plan, ws_task, leaf_task = _oversized_leaf_plan()
        trigger = RecordingReplanTrigger(slice_disposition=SliceDisposition.ASKED)
        store = ApprovalStore()
        escalation = SliceEscalationService(approvals=store, clock=FakeClock())
        service, backend = await _seed(
            plan,
            ws_task,
            leaf_task,
            config_resolver=_resolver(enabled=True),
            replan_trigger=trigger,
            slice_escalation=escalation,
        )

        await service.recompute(plan.id)
        await service.recompute(plan.id)

        fresh = await backend.plans.get(str(plan.id))
        assert fresh is not None
        assert fresh.status is PlanStatus.EXECUTING
        pending = await store.list_items(
            status=ApprovalStatus.PENDING,
            action_type=NotBlankStr(INITIATIVE_SLICE_ACTION_TYPE),
        )
        assert len(pending) == 1
        # The second pass read the open decision straight from the store and
        # never re-asked the trigger at all.
        assert trigger.slices_considered == [(str(plan.id), _LEAF)]

    async def test_a_settled_rejection_promotes_without_asking_again(self) -> None:
        plan, ws_task, leaf_task = _oversized_leaf_plan()
        trigger = RecordingReplanTrigger(slice_disposition=SliceDisposition.ASKED)
        store = ApprovalStore()
        escalation = SliceEscalationService(approvals=store, clock=FakeClock())
        workstream, leaf = plan.items
        await escalation.escalate(plan, workstream, leaf)
        rejected = (
            await store.list_items(
                status=ApprovalStatus.PENDING,
                action_type=NotBlankStr(INITIATIVE_SLICE_ACTION_TYPE),
            )
        )[0].model_copy(update={"status": ApprovalStatus.REJECTED})
        await store.save(rejected)
        service, backend = await _seed(
            plan,
            ws_task,
            leaf_task,
            config_resolver=_resolver(enabled=True),
            replan_trigger=trigger,
            slice_escalation=escalation,
        )

        await service.recompute(plan.id)

        fresh = await backend.plans.get(str(plan.id))
        assert fresh is not None
        assert fresh.status is PlanStatus.INTEGRATING
        assert trigger.slices_considered == []


class TestSliceVersusStall:
    """A genuine stall takes the existing stall route, never the slice one."""

    async def test_a_dead_leaf_is_a_stall_not_a_slice_ask(self) -> None:
        workstream = _item(_WORKSTREAM)
        leaf = _item(_LEAF, parent_id=_WORKSTREAM, unsplit_reason="depth backstop")
        plan = _plan(workstream, leaf)
        trigger = RecordingReplanTrigger()
        service, _ = await _seed(
            plan,
            _task(_WORKSTREAM, TaskStatus.FAILED),
            _task(_LEAF, TaskStatus.FAILED),
            config_resolver=_resolver(enabled=True),
            replan_trigger=trigger,
        )

        await service.recompute(plan.id)

        assert trigger.slices_considered == []
        assert len(trigger.fired) == 1

    async def test_an_atomic_leaf_needs_no_slice(self) -> None:
        workstream = _item(_WORKSTREAM)
        leaf = _item(_LEAF, parent_id=_WORKSTREAM)
        plan = _plan(workstream, leaf)
        trigger = RecordingReplanTrigger()
        service, backend = await _seed(
            plan,
            _task(_WORKSTREAM, TaskStatus.COMPLETED),
            _task(_LEAF, TaskStatus.COMPLETED),
            config_resolver=_resolver(enabled=True),
            replan_trigger=trigger,
        )

        await service.recompute(plan.id)

        fresh = await backend.plans.get(str(plan.id))
        assert fresh is not None
        assert fresh.status is PlanStatus.INTEGRATING
        assert trigger.slices_considered == []
