"""Tests for the INTEGRATE stage: one accountable, gated assembly job."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, Stakes, TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.integrate import (
    ACTOR,
    IntegrationStageService,
    escalated_stakes,
    integration_task_id,
)
from synthorg.engine.initiative.tail_stages import (
    IntegrationOutcome,
    read_integration_outcome,
)
from synthorg.engine.pipeline.models import (
    ExecutionPath,
    RoutingVerdict,
    WorkItem,
    WorkPhaseResult,
    WorkPipelineResult,
    WorkSource,
)
from synthorg.engine.pipeline.protocol import WorkPipeline
from synthorg.engine.task_engine import TaskEngine
from synthorg.settings.resolver import ConfigResolver
from tests._shared import FakeClock, as_uuid, mock_of, sid
from tests.unit.api.fakes_backend import FakePersistenceBackend

pytestmark = pytest.mark.unit

_PLAN_ID = "plan-1"
_PROJECT = "proj-1"
_PARENT = sid("parent-1")
_ITEM_A = sid("item-a")


def _item(item_id: str, *, stakes: Stakes = Stakes.NORMAL) -> PlanItem:
    return PlanItem(
        id=NotBlankStr(item_id),
        title=NotBlankStr(f"Item {item_id[:4]}"),
        description=NotBlankStr("Do the thing"),
        acceptance_criteria=(NotBlankStr("it is done"),),
        expected_artifacts=(NotBlankStr("src/thing.py"),),
        stakes=stakes,
    )


def _plan(*items: PlanItem, status: PlanStatus = PlanStatus.INTEGRATING) -> Plan:
    now = datetime(2026, 7, 24, tzinfo=UTC)
    return Plan(
        id=as_uuid(_PLAN_ID),
        project=NotBlankStr(sid(_PROJECT)),
        objective_id=NotBlankStr("obj-1"),
        objective_title=NotBlankStr("Ship the game"),
        parent_task_id=NotBlankStr(_PARENT),
        items=items or (_item(_ITEM_A),),
        status=status,
        objective_criteria=(NotBlankStr("the game is playable"),),
        created_at=now,
        updated_at=now,
    )


def _objective() -> Task:
    return Task(
        id=UUID(_PARENT),
        title="Objective",
        description="Ship the game",
        type=TaskType.DEVELOPMENT,
        priority=Priority.HIGH,
        project=sid(_PROJECT),
        created_by="ceo",
        assigned_to=sid("coordinator"),
        status=TaskStatus.IN_PROGRESS,
    )


def _pipeline_result() -> WorkPipelineResult:
    work_item = WorkItem(
        origin_adapter_id=NotBlankStr("initiative-tail"),
        source=WorkSource.OBJECTIVE,
        title=NotBlankStr("Integrate: Ship the game"),
        raw_intent=NotBlankStr("Assemble it"),
        project=NotBlankStr(sid(_PROJECT)),
        requested_by=NotBlankStr(ACTOR),
        leaf_required=True,
    )
    return WorkPipelineResult(
        work_item=work_item,
        verdict=RoutingVerdict.LEAF,
        execution_path=ExecutionPath.SOLO,
        task_id=NotBlankStr(integration_task_id(_plan())),
        final_task_status=TaskStatus.IN_REVIEW,
        phases=(
            WorkPhaseResult(
                phase=NotBlankStr("execute"), success=True, duration_seconds=1.0
            ),
        ),
        total_duration_seconds=1.0,
    )


async def _seed(
    plan: Plan,
    *tasks: Task,
    objective_missing: bool = False,
) -> tuple[IntegrationStageService, FakePersistenceBackend, AsyncMock]:
    """Build the stage over a seeded backend.

    Returns:
        The service, the backend, and the pipeline's continue mock.
    """
    backend = FakePersistenceBackend()
    await backend.plans.save(plan)
    for task in tasks:
        await backend.tasks.save(task)
    continue_from_intake = AsyncMock(return_value=_pipeline_result())
    service = IntegrationStageService(
        persistence=backend,
        task_engine=mock_of[TaskEngine](
            get_task=AsyncMock(
                return_value=None if objective_missing else _objective()
            ),
        ),
        pipeline=mock_of[WorkPipeline](continue_from_intake=continue_from_intake),
        config_resolver=mock_of[ConfigResolver](
            get_float=AsyncMock(return_value=30.0),
        ),
        clock=FakeClock(),
    )
    return service, backend, continue_from_intake


async def _fire(service: IntegrationStageService, plan: Plan) -> None:
    """Schedule the stage and wait for the detached task to finish."""
    service.schedule(plan=plan)
    await service.drain(timeout_sec=5.0)


class TestMinting:
    """The assembly job the stage creates."""

    async def test_the_task_is_minted_and_dispatched(self) -> None:
        plan = _plan()
        service, backend, dispatched = await _seed(plan)

        await _fire(service, plan)

        task = await backend.tasks.get(integration_task_id(plan))
        assert task is not None
        assert dispatched.await_count == 1
        assert dispatched.await_args is not None
        work_item, passed = dispatched.await_args.args
        assert passed.id == task.id
        assert work_item.leaf_required is True

    async def test_the_task_belongs_to_the_plan_but_no_item(self) -> None:
        """A plan_item_id would make every item derivation count it."""
        plan = _plan()
        service, backend, _ = await _seed(plan)

        await _fire(service, plan)

        task = await backend.tasks.get(integration_task_id(plan))
        assert task is not None
        assert task.plan_id == plan.id
        assert task.plan_item_id is None
        assert task.parent_task_id == plan.parent_task_id

    async def test_the_task_declares_the_artifacts_that_arm_the_guard(self) -> None:
        """A chat-only integration must terminate NO_OP, not look finished."""
        plan = _plan()
        service, backend, _ = await _seed(plan)

        await _fire(service, plan)

        task = await backend.tasks.get(integration_task_id(plan))
        assert task is not None
        assert len(task.artifacts_expected) == 2
        assert any("test" in a.path.lower() for a in task.artifacts_expected)

    async def test_the_task_carries_the_objective_criteria(self) -> None:
        plan = _plan()
        service, backend, _ = await _seed(plan)

        await _fire(service, plan)

        task = await backend.tasks.get(integration_task_id(plan))
        assert task is not None
        assert [c.description for c in task.acceptance_criteria] == [
            "the game is playable"
        ]

    async def test_the_task_runs_above_the_plans_highest_stakes(self) -> None:
        plan = _plan(_item(_ITEM_A, stakes=Stakes.NORMAL))
        service, backend, _ = await _seed(plan)

        await _fire(service, plan)

        task = await backend.tasks.get(integration_task_id(plan))
        assert task is not None
        assert task.stakes is Stakes.HIGH

    def test_stakes_escalation_is_capped(self) -> None:
        plan = _plan(_item(_ITEM_A, stakes=Stakes.CRITICAL))
        assert escalated_stakes(plan) is Stakes.CRITICAL


class TestGuards:
    """Every reason the stage declines to mint."""

    async def test_an_existing_task_is_not_minted_twice(self) -> None:
        """The rollup fires on every recompute that reads INTEGRATING."""
        plan = _plan()
        service, _, dispatched = await _seed(plan)

        await _fire(service, plan)
        await _fire(service, plan)

        assert dispatched.await_count == 1

    async def test_a_plan_that_left_integrating_is_not_minted(self) -> None:
        plan = _plan(status=PlanStatus.EXECUTING)
        service, backend, dispatched = await _seed(plan)

        await _fire(service, plan)

        assert dispatched.await_count == 0
        assert await backend.tasks.get(integration_task_id(plan)) is None

    async def test_a_missing_objective_task_is_not_minted(self) -> None:
        plan = _plan()
        service, backend, dispatched = await _seed(plan, objective_missing=True)

        await _fire(service, plan)

        assert dispatched.await_count == 0
        assert await backend.tasks.get(integration_task_id(plan)) is None

    async def test_a_dispatch_failure_never_escapes(self) -> None:
        """The stage is best-effort; the plan stays INTEGRATING and re-fires."""
        plan = _plan()
        service, _, dispatched = await _seed(plan)
        dispatched.side_effect = RuntimeError("no coordinator")

        await _fire(service, plan)

        assert dispatched.await_count == 1


class TestOutcome:
    """Reading where the assembly job got to."""

    @staticmethod
    def _integration_task(status: TaskStatus) -> Task:
        return Task(
            id=UUID(integration_task_id(_plan())),
            title="Integrate",
            description="Assemble it",
            type=TaskType.DEVELOPMENT,
            priority=Priority.HIGH,
            project=sid(_PROJECT),
            plan_id=as_uuid(_PLAN_ID),
            created_by=ACTOR,
            assigned_to=sid("agent-1"),
            status=status,
        )

    async def test_absent_when_no_task_exists(self) -> None:
        backend = FakePersistenceBackend()

        outcome = await read_integration_outcome(
            backend, task_id=integration_task_id(_plan())
        )

        assert outcome is IntegrationOutcome.ABSENT

    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (TaskStatus.IN_PROGRESS, IntegrationOutcome.RUNNING),
            (TaskStatus.IN_REVIEW, IntegrationOutcome.RUNNING),
            (TaskStatus.COMPLETED, IntegrationOutcome.PASSED),
            (TaskStatus.FAILED, IntegrationOutcome.FAILED),
            (TaskStatus.REJECTED, IntegrationOutcome.FAILED),
            (TaskStatus.CANCELLED, IntegrationOutcome.FAILED),
        ],
        ids=lambda value: str(value.value),
    )
    async def test_outcome_follows_persisted_status(
        self, status: TaskStatus, expected: IntegrationOutcome
    ) -> None:
        """IN_REVIEW is RUNNING: the oracle chain has not ruled on it yet."""
        backend = FakePersistenceBackend()
        await backend.tasks.save(self._integration_task(status))

        outcome = await read_integration_outcome(
            backend, task_id=integration_task_id(_plan())
        )

        assert outcome is expected
