"""The INTEGRATE stage the rollup drives, and what it refuses to skip."""

from uuid import UUID

import pytest

from synthorg.core.plan import Plan
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.completion import StallReason
from synthorg.engine.initiative.tail_stages import integration_task_id
from tests._shared import (
    RecordingReplanTrigger as _RecordingReplanTrigger,
)
from tests._shared import (
    as_uuid,
    sid,
)
from tests.unit.engine.initiative._rollup_fixtures import (
    ITEM_A as _ITEM_A,
)
from tests.unit.engine.initiative._rollup_fixtures import (
    PLAN_ID as _PLAN_ID,
)
from tests.unit.engine.initiative._rollup_fixtures import (
    PROJECT as _PROJECT,
)
from tests.unit.engine.initiative._rollup_fixtures import (
    item as _item,
)
from tests.unit.engine.initiative._rollup_fixtures import (
    plan_of as _plan,
)
from tests.unit.engine.initiative._rollup_fixtures import (
    seed as _seed,
)
from tests.unit.engine.initiative._rollup_fixtures import (
    statuses as _statuses,
)
from tests.unit.engine.initiative._rollup_fixtures import (
    task_of as _task,
)

pytestmark = pytest.mark.unit


class _RecordingIntegration:
    """An integration stage that records the plans it was fired for."""

    def __init__(self) -> None:
        self.fired: list[str] = []
        self.attempts: list[int] = []
        self.drained: list[float] = []

    def schedule(self, *, plan: Plan, attempt: int = 0) -> None:
        self.fired.append(str(plan.id))
        self.attempts.append(attempt)

    async def drain(self, *, timeout_sec: float) -> None:
        self.drained.append(timeout_sec)


def _integration_task(status: TaskStatus, attempt: int = 0) -> Task:
    """Build the plan's integration task, which implements no plan item.

    Returns:
        The assembly task at *status*.
    """
    return Task(
        id=UUID(integration_task_id(_plan(_item(_ITEM_A)), attempt)),
        title="Integrate",
        description="Assemble it",
        type=TaskType.DEVELOPMENT,
        priority=Priority.HIGH,
        project=sid(_PROJECT),
        plan_id=as_uuid(_PLAN_ID),
        created_by="initiative-integrate",
        assigned_to=sid("agent-1"),
        status=status,
    )


class TestIntegrationStage:
    """The plan cannot leave INTEGRATING without an assembly job that passed."""

    async def test_the_stage_is_fired_when_no_assembly_job_exists(self) -> None:
        integration = _RecordingIntegration()
        service, backend = await _seed(
            _plan(_item(_ITEM_A), status=PlanStatus.INTEGRATING),
            _task(_ITEM_A, TaskStatus.COMPLETED),
            integration=integration,
        )

        await service.recompute(as_uuid(_PLAN_ID))

        assert integration.fired == [sid(_PLAN_ID)]
        plan_status, _ = await _statuses(backend)
        assert plan_status is PlanStatus.INTEGRATING

    async def test_a_running_assembly_job_holds_the_plan(self) -> None:
        integration = _RecordingIntegration()
        service, backend = await _seed(
            _plan(_item(_ITEM_A), status=PlanStatus.INTEGRATING),
            _task(_ITEM_A, TaskStatus.COMPLETED),
            _integration_task(TaskStatus.IN_REVIEW),
            integration=integration,
        )

        await service.recompute(as_uuid(_PLAN_ID))

        assert integration.fired == []
        plan_status, _ = await _statuses(backend)
        assert plan_status is PlanStatus.INTEGRATING

    async def test_a_passed_assembly_job_opens_evaluation(self) -> None:
        service, backend = await _seed(
            _plan(_item(_ITEM_A), status=PlanStatus.INTEGRATING),
            _task(_ITEM_A, TaskStatus.COMPLETED),
            _integration_task(TaskStatus.COMPLETED),
            integration=_RecordingIntegration(),
        )

        await service.recompute(as_uuid(_PLAN_ID))

        assert await _statuses(backend) == (
            PlanStatus.EVALUATING,
            ProjectStatus.EVALUATING,
        )

    async def test_a_failed_assembly_job_replans(self) -> None:
        """No derivation over items can see this: every item is COMPLETED."""
        trigger = _RecordingReplanTrigger()
        service, backend = await _seed(
            _plan(_item(_ITEM_A), status=PlanStatus.INTEGRATING),
            _task(_ITEM_A, TaskStatus.COMPLETED),
            _integration_task(TaskStatus.REJECTED),
            integration=_RecordingIntegration(),
            replan_trigger=trigger,
        )

        await service.recompute(as_uuid(_PLAN_ID))

        assert trigger.fired == [(sid(_PLAN_ID), StallReason.INTEGRATION_FAILED)]
        plan_status, _ = await _statuses(backend)
        assert plan_status is PlanStatus.INTEGRATING

    async def test_an_unwired_stage_parks_the_plan_rather_than_completing_it(
        self,
    ) -> None:
        """An initiative nobody assembled has not delivered anything."""
        service, backend = await _seed(
            _plan(_item(_ITEM_A), status=PlanStatus.INTEGRATING),
            _task(_ITEM_A, TaskStatus.COMPLETED),
        )

        await service.recompute(as_uuid(_PLAN_ID))

        plan_status, _ = await _statuses(backend)
        assert plan_status is PlanStatus.INTEGRATING

    async def test_the_assembly_job_does_not_count_as_a_plan_item(self) -> None:
        """It carries plan_id but no plan_item_id, so derivations ignore it."""
        service, backend = await _seed(
            _plan(_item(_ITEM_A), status=PlanStatus.INTEGRATING),
            _task(_ITEM_A, TaskStatus.COMPLETED),
            _integration_task(TaskStatus.FAILED),
            integration=_RecordingIntegration(),
        )

        await service.recompute(as_uuid(_PLAN_ID))

        # A failed integration task counted as an item would regress the plan
        # to EXECUTING; it must not. With nothing able to route or escalate the
        # failed assembly, the plan fails rather than parking at INTEGRATING.
        plan = await backend.plans.get(NotBlankStr(sid(_PLAN_ID)))
        assert plan is not None
        assert plan.status is PlanStatus.FAILED
        # The reason, not merely the status: an item-derived stall also lands
        # on FAILED here, so asserting the status alone would still pass if
        # the assembly job started counting as a plan item.
        assert plan.failure_reason == "initiative stalled: integration_failed"

    async def test_a_failed_assembly_replans_when_a_trigger_is_wired(self) -> None:
        trigger = _RecordingReplanTrigger()
        service, backend = await _seed(
            _plan(_item(_ITEM_A), status=PlanStatus.INTEGRATING),
            _task(_ITEM_A, TaskStatus.COMPLETED),
            _integration_task(TaskStatus.FAILED),
            integration=_RecordingIntegration(),
            replan_trigger=trigger,
        )

        await service.recompute(as_uuid(_PLAN_ID))

        assert trigger.fired == [
            (str(as_uuid(_PLAN_ID)), StallReason.INTEGRATION_FAILED)
        ]
        plan_status, _ = await _statuses(backend)
        assert plan_status is PlanStatus.INTEGRATING

    async def test_drain_delegates_to_the_stage(self) -> None:
        integration = _RecordingIntegration()
        service, _ = await _seed(
            _plan(_item(_ITEM_A)),
            _task(_ITEM_A, TaskStatus.COMPLETED),
            integration=integration,
        )

        await service.drain_integration(timeout_sec=5.0)

        assert integration.drained == [5.0]

    async def test_drain_is_a_noop_without_a_wired_stage(self) -> None:
        service, _ = await _seed(
            _plan(_item(_ITEM_A)),
            _task(_ITEM_A, TaskStatus.COMPLETED),
        )

        await service.drain_integration(timeout_sec=5.0)
