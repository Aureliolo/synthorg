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
from synthorg.engine.artifacts.expected_artifact_check import is_probeable_path
from synthorg.engine.initiative.integrate import (
    ACTOR,
    IntegrationStageService,
    escalated_stakes,
)
from synthorg.engine.initiative.integrate_brief import (
    INTEGRATION_REPORT_PATH,
    INTEGRATION_TEST_OUTPUT_PATH,
)
from synthorg.engine.initiative.tail_stages import (
    MAX_INTEGRATION_ATTEMPTS,
    IntegrationOutcome,
    integration_task_id,
    read_integration_state,
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
        task_id=NotBlankStr(integration_task_id(_plan(), 0)),
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

    async def _run_it(_work_item: WorkItem, task: Task) -> WorkPipelineResult:
        # The real spine runs the task inline and leaves it past CREATED. The
        # stage distinguishes "never started" from "under way" on exactly that,
        # so a mock that left it at CREATED would not be a faithful stand-in.
        await backend.tasks.save(
            task.model_copy(
                update={
                    "status": TaskStatus.IN_REVIEW,
                    "assigned_to": sid("agent-1"),
                }
            )
        )
        return _pipeline_result()

    continue_from_intake = AsyncMock(side_effect=_run_it)
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


async def _fire(
    service: IntegrationStageService,
    plan: Plan,
    attempt: int = 0,
) -> None:
    """Schedule the stage and wait for the detached task to finish.

    Settles rather than drains: a drain closes the stage for good, which is
    right at shutdown and wrong for a test that fires it more than once.
    """
    service.schedule(plan=plan, attempt=attempt)
    await service.settle(timeout_sec=5.0)


class TestMinting:
    """The assembly job the stage creates."""

    async def test_the_task_is_minted_and_dispatched(self) -> None:
        plan = _plan()
        service, backend, dispatched = await _seed(plan)

        await _fire(service, plan)

        task = await backend.tasks.get(integration_task_id(plan, 0))
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

        task = await backend.tasks.get(integration_task_id(plan, 0))
        assert task is not None
        assert task.plan_id == plan.id
        assert task.plan_item_id is None
        assert task.parent_task_id == plan.parent_task_id

    async def test_the_task_declares_the_artifacts_that_arm_the_guard(self) -> None:
        """A chat-only integration must terminate NO_OP, not look finished.

        The declarations are workspace-relative paths, because the check can
        only probe a path: prose declarations leave it abstaining, which is
        indistinguishable from a run that delivered.
        """
        plan = _plan()
        service, backend, _ = await _seed(plan)

        await _fire(service, plan)

        task = await backend.tasks.get(integration_task_id(plan, 0))
        assert task is not None
        declared = [a.path for a in task.artifacts_expected]
        assert declared == [
            INTEGRATION_REPORT_PATH,
            INTEGRATION_TEST_OUTPUT_PATH,
        ]
        assert all(is_probeable_path(path) for path in declared)

    async def test_the_task_carries_the_objective_criteria(self) -> None:
        plan = _plan()
        service, backend, _ = await _seed(plan)

        await _fire(service, plan)

        task = await backend.tasks.get(integration_task_id(plan, 0))
        assert task is not None
        assert [c.description for c in task.acceptance_criteria] == [
            "the game is playable"
        ]

    async def test_the_task_runs_above_the_plans_highest_stakes(self) -> None:
        plan = _plan(_item(_ITEM_A, stakes=Stakes.NORMAL))
        service, backend, _ = await _seed(plan)

        await _fire(service, plan)

        task = await backend.tasks.get(integration_task_id(plan, 0))
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
        assert await backend.tasks.get(integration_task_id(plan, 0)) is None

    async def test_a_missing_objective_task_is_not_minted(self) -> None:
        plan = _plan()
        service, backend, dispatched = await _seed(plan, objective_missing=True)

        await _fire(service, plan)

        assert dispatched.await_count == 0
        assert await backend.tasks.get(integration_task_id(plan, 0)) is None

    async def test_a_dispatch_failure_never_escapes(self) -> None:
        """The stage is best-effort; the plan stays INTEGRATING."""
        plan = _plan()
        service, _, dispatched = await _seed(plan)
        dispatched.side_effect = RuntimeError("no coordinator")

        await _fire(service, plan)

        assert dispatched.await_count == 1

    async def test_a_dispatch_failure_leaves_a_re_dispatchable_row(self) -> None:
        """The row is saved before the pipeline is handed the task.

        A dispatch that dies in between leaves a task nothing is driving, and
        treating its mere existence as "already minted" would park the plan at
        INTEGRATING for good.
        """
        plan = _plan()
        service, backend, dispatched = await _seed(plan)
        working = dispatched.side_effect
        dispatched.side_effect = RuntimeError("no coordinator")
        await _fire(service, plan)
        dispatched.side_effect = working

        await _fire(service, plan)

        assert dispatched.await_count == 2
        state = await read_integration_state(backend, plan, allow_new_attempt=False)
        assert state.attempt == 0

    async def test_a_burst_of_schedules_collapses_to_one_attempt(self) -> None:
        """The rollup fires on every recompute, not on an edge."""
        plan = _plan()
        service, _, dispatched = await _seed(plan)

        service.schedule(plan=plan)
        service.schedule(plan=plan)
        await service.drain(timeout_sec=5.0)

        assert dispatched.await_count == 1

    async def test_a_foreign_task_at_the_derived_id_is_refused(self) -> None:
        """Plan item ids are caller-supplied, and the id is derivable."""
        plan = _plan()
        impostor = Task(
            id=UUID(integration_task_id(plan, 0)),
            title="Not the assembly job",
            description="Filed by a caller",
            type=TaskType.DEVELOPMENT,
            priority=Priority.HIGH,
            project=sid(_PROJECT),
            plan_id=plan.id,
            plan_item_id=as_uuid(_ITEM_A),
            created_by="someone-else",
            assigned_to=sid("agent-1"),
            status=TaskStatus.COMPLETED,
        )
        service, backend, dispatched = await _seed(plan, impostor)

        await _fire(service, plan)

        assert dispatched.await_count == 0
        state = await read_integration_state(backend, plan, allow_new_attempt=False)
        assert state.outcome is IntegrationOutcome.FAILED


class TestOutcome:
    """Reading where the assembly job got to."""

    @staticmethod
    def _integration_task(status: TaskStatus, attempt: int = 0) -> Task:
        return Task(
            id=UUID(integration_task_id(_plan(), attempt)),
            title="Integrate",
            description="Assemble it",
            type=TaskType.DEVELOPMENT,
            priority=Priority.HIGH,
            project=sid(_PROJECT),
            plan_id=as_uuid(_PLAN_ID),
            created_by=ACTOR,
            # A CREATED task is by definition unassigned; every later status
            # requires an assignee.
            assigned_to=None if status is TaskStatus.CREATED else sid("agent-1"),
            status=status,
        )

    async def test_absent_when_no_task_exists(self) -> None:
        backend = FakePersistenceBackend()

        state = await read_integration_state(backend, _plan(), allow_new_attempt=False)

        assert state.outcome is IntegrationOutcome.ABSENT
        assert state.attempt == 0

    @pytest.mark.parametrize(
        ("status", "expected"),
        [
            (TaskStatus.CREATED, IntegrationOutcome.PENDING),
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

        state = await read_integration_state(backend, _plan(), allow_new_attempt=False)

        assert state.outcome is expected

    async def test_a_spent_attempt_is_stepped_over_only_on_re_entry(self) -> None:
        """Otherwise a failed assembly re-runs itself on every rollup event."""
        backend = FakePersistenceBackend()
        await backend.tasks.save(self._integration_task(TaskStatus.FAILED))

        held = await read_integration_state(backend, _plan(), allow_new_attempt=False)
        reworked = await read_integration_state(
            backend, _plan(), allow_new_attempt=True
        )

        assert held.outcome is IntegrationOutcome.FAILED
        assert held.attempt == 0
        assert reworked.outcome is IntegrationOutcome.ABSENT
        assert reworked.attempt == 1

    async def test_attempts_are_capped(self) -> None:
        """Repeated assembly failures are a planning problem, not a retry one."""
        backend = FakePersistenceBackend()
        for attempt in range(MAX_INTEGRATION_ATTEMPTS):
            await backend.tasks.save(self._integration_task(TaskStatus.FAILED, attempt))

        state = await read_integration_state(backend, _plan(), allow_new_attempt=True)

        assert state.outcome is IntegrationOutcome.FAILED
        assert state.attempt == MAX_INTEGRATION_ATTEMPTS - 1
