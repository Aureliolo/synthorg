"""The SKELETON stage the rollup drives, and what it refuses to skip.

The head stage is what makes the contract unskippable: a plan cannot reach
EXECUTING except by passing it, so every question about when a unit becomes
dispatchable is answered here.
"""

from uuid import UUID

import pytest

from synthorg.core.plan import Plan
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.completion import StallReason
from synthorg.engine.initiative.head_stages import (
    MAX_SKELETON_ATTEMPTS,
    SKELETON_ACTOR,
    skeleton_task_id,
)
from synthorg.engine.initiative.ports import DriveOutcome
from synthorg.engine.initiative.rollup import ProjectRollupService
from tests._shared import (
    RecordingReplanTrigger as _RecordingReplanTrigger,
)
from tests._shared import (
    as_uuid,
    sid,
)
from tests.unit.api.fakes_backend import FakePersistenceBackend
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


class _RecordingSkeleton:
    """A skeleton stage that records the plans it was fired for."""

    def __init__(self) -> None:
        self.fired: list[str] = []
        self.attempts: list[int] = []
        self.drained: list[float] = []

    def schedule(self, *, plan: Plan, attempt: int = 0) -> None:
        self.fired.append(str(plan.id))
        self.attempts.append(attempt)

    async def drain(self, *, timeout_sec: float) -> None:
        self.drained.append(timeout_sec)


class _RecordingDriver:
    """A plan driver that records what it was asked to drive, and answers."""

    def __init__(self, outcome: DriveOutcome = DriveOutcome.DRIVING) -> None:
        self.driven: list[str] = []
        self._outcome = outcome

    async def __call__(self, plan: Plan) -> DriveOutcome:
        self.driven.append(str(plan.id))
        return self._outcome


async def _staged(
    plan: Plan,
    *tasks: Task,
    skeleton: _RecordingSkeleton | None = None,
    plan_driver: _RecordingDriver | None = None,
    replan_trigger: _RecordingReplanTrigger | None = None,
) -> tuple[ProjectRollupService, FakePersistenceBackend]:
    """Seed the rollup and attach the head stage the way boot does.

    Attached rather than constructed: the rollup is wired before setup has
    configured a provider, so every stage arrives through ``attach_tail`` on
    its own subsystem's schedule, and a test that constructs one instead is
    exercising a shape production never builds.

    Returns:
        The rollup and the backend it was built over.
    """
    service, backend = await _seed(plan, *tasks, replan_trigger=replan_trigger)
    service.attach_tail(skeleton=skeleton, plan_driver=plan_driver)
    return service, backend


def _skeleton_task(status: TaskStatus, attempt: int = 0) -> Task:
    """Build the plan's skeleton task, which implements no plan item.

    Returns:
        The contract task at *status*.
    """
    return Task(
        id=UUID(
            skeleton_task_id(_plan(_item(_ITEM_A), status=PlanStatus.SKELETON), attempt)
        ),
        title="Skeleton: Ship it",
        description="Write the contract",
        type=TaskType.DEVELOPMENT,
        priority=Priority.HIGH,
        project=sid(_PROJECT),
        plan_id=as_uuid(_PLAN_ID),
        created_by=SKELETON_ACTOR,
        assigned_to=sid("agent-1") if status is not TaskStatus.CREATED else None,
        status=status,
    )


class TestFiringTheStage:
    async def test_a_plan_at_skeleton_with_no_attempt_fires_the_stage(self) -> None:
        plan = _plan(_item(_ITEM_A), status=PlanStatus.SKELETON)
        stage = _RecordingSkeleton()
        service, _ = await _staged(
            plan, _task(_ITEM_A, TaskStatus.CREATED), skeleton=stage
        )

        await service.recompute(as_uuid(_PLAN_ID))

        assert stage.fired == [str(as_uuid(_PLAN_ID))]
        assert stage.attempts == [0]

    async def test_a_running_job_is_not_fired_again(self) -> None:
        """The job's own row is the record, so a second recompute reads it."""
        plan = _plan(_item(_ITEM_A), status=PlanStatus.SKELETON)
        stage = _RecordingSkeleton()
        service, _ = await _staged(
            plan,
            _task(_ITEM_A, TaskStatus.CREATED),
            _skeleton_task(TaskStatus.IN_PROGRESS),
            skeleton=stage,
        )

        await service.recompute(as_uuid(_PLAN_ID))

        assert stage.fired == []

    async def test_an_unwired_stage_parks_the_plan_rather_than_advancing(
        self,
    ) -> None:
        """An initiative whose contract was never written has not been built.

        Advancing past it would dispatch every unit against nothing, which is
        the entire failure the stage exists to remove.
        """
        plan = _plan(_item(_ITEM_A), status=PlanStatus.SKELETON)
        service, backend = await _staged(plan, _task(_ITEM_A, TaskStatus.CREATED))

        await service.recompute(as_uuid(_PLAN_ID))

        plan_status, _project_status = await _statuses(backend)
        assert plan_status is PlanStatus.SKELETON


class TestWhenTheContractPasses:
    async def test_the_plan_reaches_executing(self) -> None:
        plan = _plan(_item(_ITEM_A), status=PlanStatus.SKELETON)
        service, backend = await _staged(
            plan,
            _task(_ITEM_A, TaskStatus.CREATED),
            _skeleton_task(TaskStatus.COMPLETED),
            skeleton=_RecordingSkeleton(),
        )

        await service.recompute(as_uuid(_PLAN_ID))

        plan_status, project_status = await _statuses(backend)
        assert plan_status is PlanStatus.EXECUTING
        assert project_status is ProjectStatus.ACTIVE

    async def test_the_units_are_driven_on_that_edge_and_no_other(self) -> None:
        """This is the first moment a unit is dispatchable, so it is the moment.

        Without it the plan reaches EXECUTING with nothing running and waits
        for a recovery sweep to notice, which is minutes of silence on the
        ordinary path.
        """
        plan = _plan(_item(_ITEM_A), status=PlanStatus.SKELETON)
        driver = _RecordingDriver()
        service, _ = await _staged(
            plan,
            _task(_ITEM_A, TaskStatus.CREATED),
            _skeleton_task(TaskStatus.COMPLETED),
            skeleton=_RecordingSkeleton(),
            plan_driver=driver,
        )

        await service.recompute(as_uuid(_PLAN_ID))

        assert driver.driven == [str(as_uuid(_PLAN_ID))]

    async def test_a_plan_already_at_executing_is_not_driven_from_here(self) -> None:
        """Only the passing edge dispatches, so a later event cannot re-drive."""
        plan = _plan(_item(_ITEM_A), status=PlanStatus.EXECUTING)
        driver = _RecordingDriver()
        service, _ = await _staged(
            plan,
            _task(_ITEM_A, TaskStatus.IN_PROGRESS),
            skeleton=_RecordingSkeleton(),
            plan_driver=driver,
        )

        await service.recompute(as_uuid(_PLAN_ID))

        assert driver.driven == []

    async def test_a_driver_that_refuses_routes_the_plan_to_a_stall(self) -> None:
        """A refusal is not recoverable, so leaving it as a delay is wrong.

        The recovery sweep would report rescuing the plan on every pass while
        nothing ran, for ever. A refusal is what a dispatch failure was before
        the contract stage existed, and it routes the same way.
        """
        plan = _plan(_item(_ITEM_A), status=PlanStatus.SKELETON)
        replan = _RecordingReplanTrigger()
        service, backend = await _staged(
            plan,
            _task(_ITEM_A, TaskStatus.CREATED),
            _skeleton_task(TaskStatus.COMPLETED),
            skeleton=_RecordingSkeleton(),
            plan_driver=_RecordingDriver(DriveOutcome.REFUSED),
            replan_trigger=replan,
        )

        await service.recompute(as_uuid(_PLAN_ID))

        assert replan.fired == [(str(as_uuid(_PLAN_ID)), StallReason.SKELETON_FAILED)]
        plan_status, _project_status = await _statuses(backend)
        assert plan_status is not PlanStatus.SKELETON

    async def test_a_driver_that_already_holds_the_plan_changes_nothing(self) -> None:
        """Two drivers on one plan assign the same subtasks and the second loses.

        Being told somebody else holds it is correct and will finish, so it is
        left alone rather than stalled.
        """
        plan = _plan(_item(_ITEM_A), status=PlanStatus.SKELETON)
        replan = _RecordingReplanTrigger()
        service, backend = await _staged(
            plan,
            _task(_ITEM_A, TaskStatus.CREATED),
            _skeleton_task(TaskStatus.COMPLETED),
            skeleton=_RecordingSkeleton(),
            plan_driver=_RecordingDriver(DriveOutcome.HELD),
            replan_trigger=replan,
        )

        await service.recompute(as_uuid(_PLAN_ID))

        assert replan.fired == []
        plan_status, _project_status = await _statuses(backend)
        assert plan_status is PlanStatus.EXECUTING

    async def test_an_unwired_driver_leaves_the_plan_for_a_recovery_sweep(
        self,
    ) -> None:
        """The contract passed, so the plan has earned EXECUTING regardless.

        Holding it at SKELETON because the driver is not up would re-run the
        contract job on the next pass against a plan that already has one.
        """
        plan = _plan(_item(_ITEM_A), status=PlanStatus.SKELETON)
        service, backend = await _staged(
            plan,
            _task(_ITEM_A, TaskStatus.CREATED),
            _skeleton_task(TaskStatus.COMPLETED),
            skeleton=_RecordingSkeleton(),
        )

        await service.recompute(as_uuid(_PLAN_ID))

        plan_status, _project_status = await _statuses(backend)
        assert plan_status is PlanStatus.EXECUTING


class TestWhenTheContractFails:
    async def test_it_routes_to_a_replan_rather_than_retrying_for_ever(self) -> None:
        """The cheapest failure in the run: nothing is built against it yet."""
        plan = _plan(_item(_ITEM_A), status=PlanStatus.SKELETON)
        replan = _RecordingReplanTrigger()
        service, _ = await _staged(
            plan,
            _task(_ITEM_A, TaskStatus.CREATED),
            _skeleton_task(TaskStatus.FAILED),
            skeleton=_RecordingSkeleton(),
            replan_trigger=replan,
        )

        await service.recompute(as_uuid(_PLAN_ID))

        assert replan.fired == [(str(as_uuid(_PLAN_ID)), StallReason.SKELETON_FAILED)]

    async def test_the_stall_names_the_skeleton_rather_than_the_assembly(
        self,
    ) -> None:
        """A replan reads the reason, so the wrong one is a lie it acts on."""
        plan = _plan(_item(_ITEM_A), status=PlanStatus.SKELETON)
        service, backend = await _staged(
            plan,
            _task(_ITEM_A, TaskStatus.CREATED),
            _skeleton_task(TaskStatus.FAILED),
            skeleton=_RecordingSkeleton(),
        )

        await service.recompute(as_uuid(_PLAN_ID))

        stored = await backend.plans.get(NotBlankStr(sid(_PLAN_ID)))
        assert stored is not None
        assert stored.failure_reason is not None
        assert StallReason.SKELETON_FAILED.value in stored.failure_reason

    async def test_a_spent_attempt_is_not_re_fired_on_every_event(self) -> None:
        """Re-firing would rewrite the same failed contract on every task event."""
        plan = _plan(_item(_ITEM_A), status=PlanStatus.SKELETON)
        stage = _RecordingSkeleton()
        service, _ = await _staged(
            plan,
            _task(_ITEM_A, TaskStatus.CREATED),
            _skeleton_task(TaskStatus.FAILED),
            skeleton=stage,
        )

        await service.recompute(as_uuid(_PLAN_ID))

        assert stage.fired == []


class TestTheAttemptCeiling:
    async def test_a_plan_that_exhausted_its_attempts_is_never_re_fired(
        self,
    ) -> None:
        plan = _plan(_item(_ITEM_A), status=PlanStatus.SKELETON)
        stage = _RecordingSkeleton()
        service, _ = await _staged(
            plan,
            _task(_ITEM_A, TaskStatus.CREATED),
            *(
                _skeleton_task(TaskStatus.FAILED, attempt)
                for attempt in range(MAX_SKELETON_ATTEMPTS)
            ),
            skeleton=stage,
        )

        await service.recompute(as_uuid(_PLAN_ID))

        assert stage.fired == []
