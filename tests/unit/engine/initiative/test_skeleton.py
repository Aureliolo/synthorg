"""Tests for the SKELETON stage: one accountable, gated contract job."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from synthorg.core.plan import Plan, PlanItem
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, Stakes, TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.assembly import escalated_stakes
from synthorg.engine.initiative.head_stages import (
    MAX_SKELETON_ATTEMPTS,
    is_skeleton_task,
    read_skeleton_state,
    skeleton_task_id,
    skeleton_task_uuid,
)
from synthorg.engine.initiative.skeleton import ACTOR, SkeletonStageService
from synthorg.engine.initiative.skeleton_brief import MANIFEST_PATH
from synthorg.engine.initiative.stage_state import StageOutcome
from synthorg.engine.initiative.tail_stages import integration_task_id
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
_CRITERION = "the game is playable"


def _item(item_id: str, *, stakes: Stakes = Stakes.NORMAL) -> PlanItem:
    return PlanItem(
        id=NotBlankStr(item_id),
        title=NotBlankStr(f"Item {item_id[:4]}"),
        description=NotBlankStr("Do the thing"),
        acceptance_criteria=(NotBlankStr("it is done"),),
        expected_artifacts=(NotBlankStr("src/thing.py"),),
        stakes=stakes,
    )


def _plan(*items: PlanItem, status: PlanStatus = PlanStatus.SKELETON) -> Plan:
    now = datetime(2026, 7, 24, tzinfo=UTC)
    return Plan(
        id=as_uuid(_PLAN_ID),
        project=NotBlankStr(sid(_PROJECT)),
        project_name=NotBlankStr("Games"),
        objective_id=NotBlankStr("obj-1"),
        objective_title=NotBlankStr("Ship the game"),
        parent_task_id=NotBlankStr(_PARENT),
        items=items or (_item(_ITEM_A),),
        status=status,
        objective_criteria=(NotBlankStr(_CRITERION),),
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
        origin_adapter_id=NotBlankStr("initiative-head"),
        source=WorkSource.OBJECTIVE,
        title=NotBlankStr("Skeleton: Ship the game"),
        raw_intent=NotBlankStr("Write the contract"),
        project=NotBlankStr(sid(_PROJECT)),
        requested_by=NotBlankStr(ACTOR),
        leaf_required=True,
    )
    return WorkPipelineResult(
        work_item=work_item,
        verdict=RoutingVerdict.LEAF,
        execution_path=ExecutionPath.SOLO,
        task_id=NotBlankStr(skeleton_task_id(_plan(), 0)),
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
) -> tuple[SkeletonStageService, FakePersistenceBackend, AsyncMock]:
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
    service = SkeletonStageService(
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
    service: SkeletonStageService,
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
    """The contract job the stage creates."""

    async def test_the_task_is_minted_and_dispatched_as_a_leaf(self) -> None:
        """Splitting the contract across agents is how you get two contracts."""
        plan = _plan()
        service, backend, dispatched = await _seed(plan)

        await _fire(service, plan)

        task = await backend.tasks.get(skeleton_task_id(plan, 0))
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

        task = await backend.tasks.get(skeleton_task_id(plan, 0))
        assert task is not None
        assert task.plan_id == plan.id
        assert task.plan_item_id is None

    async def test_the_actor_tells_it_apart_from_the_assembly_job(self) -> None:
        """Both stage rows carry a plan id and no item id, so nothing else can.

        Their ids are derived in different namespaces, which stops a collision,
        but a reader holding one row and asking whose it is has only the actor
        to go on.
        """
        plan = _plan()
        service, backend, _ = await _seed(plan)

        await _fire(service, plan)

        task = await backend.tasks.get(skeleton_task_id(plan, 0))
        assert task is not None
        assert task.created_by == ACTOR
        assert is_skeleton_task(task, plan) is True
        assert skeleton_task_id(plan, 0) != integration_task_id(plan, 0)

    async def test_it_declares_the_manifest_so_the_artifact_guard_arms(self) -> None:
        """A contract job that produced nothing must fail, like any other work.

        The gate configuration is the artifact that proves the job happened:
        the layout and the tests are read by people, the manifest is read by
        every later stage.
        """
        plan = _plan()
        service, backend, _ = await _seed(plan)

        await _fire(service, plan)

        task = await backend.tasks.get(skeleton_task_id(plan, 0))
        assert task is not None
        assert MANIFEST_PATH in {
            str(expected.path) for expected in task.artifacts_expected
        }

    async def test_it_carries_the_objective_criteria_it_writes_tests_for(
        self,
    ) -> None:
        plan = _plan()
        service, backend, _ = await _seed(plan)

        await _fire(service, plan)

        task = await backend.tasks.get(skeleton_task_id(plan, 0))
        assert task is not None
        assert [str(c.description) for c in task.acceptance_criteria] == [_CRITERION]

    async def test_it_is_judged_at_the_highest_stakes_any_unit_carries(self) -> None:
        """Getting a seam wrong is not a small mistake because it is a small file.

        Every unit below is briefed from this output, so the contract inherits
        the stakes of the riskiest thing standing on it rather than its own
        apparent size.
        """
        plan = _plan(
            _item(_ITEM_A, stakes=Stakes.NORMAL),
            _item(sid("item-b"), stakes=Stakes.CRITICAL),
        )
        service, backend, _ = await _seed(plan)

        await _fire(service, plan)

        task = await backend.tasks.get(skeleton_task_id(plan, 0))
        assert task is not None
        assert task.stakes is escalated_stakes([Stakes.NORMAL, Stakes.CRITICAL])


class TestIdempotency:
    """Minting twice would put two contracts on one plan."""

    async def test_a_second_fire_never_mints_a_second_job(self) -> None:
        plan = _plan()
        service, backend, dispatched = await _seed(plan)

        await _fire(service, plan)
        await _fire(service, plan)

        assert dispatched.await_count == 1
        minted = [
            task
            for task in await backend.tasks.list_items(limit=100)
            if task.created_by == ACTOR
        ]
        assert len(minted) == 1

    async def test_a_row_left_at_created_is_re_dispatched(self) -> None:
        """The row is persisted before the pipeline is handed the task.

        A dispatch that died in between leaves a row nothing is driving, and
        CREATED is exactly that case: anything further along is under way or
        finished, and the outcome read owns it from there.
        """
        plan = _plan()
        service, backend, dispatched = await _seed(plan)
        await _fire(service, plan)
        stranded = await backend.tasks.get(skeleton_task_id(plan, 0))
        assert stranded is not None
        await backend.tasks.save(
            stranded.model_copy(update={"status": TaskStatus.CREATED})
        )

        await _fire(service, plan)

        assert dispatched.await_count == 2

    async def test_a_row_already_under_way_is_left_alone(self) -> None:
        plan = _plan()
        service, _, dispatched = await _seed(plan)
        await _fire(service, plan)

        await _fire(service, plan)

        assert dispatched.await_count == 1


class TestWhenTheStageDeclines:
    """Every decline is silent to the caller and loud in the log."""

    async def test_a_plan_that_left_skeleton_mints_nothing(self) -> None:
        """The status is re-read at fire time, not trusted from the event.

        A plan that advanced between the observer firing and the detached task
        running would otherwise get a contract job for a stage it has left.
        """
        plan = _plan(status=PlanStatus.EXECUTING)
        service, backend, dispatched = await _seed(plan)

        await _fire(service, plan)

        assert dispatched.await_count == 0
        assert await backend.tasks.get(skeleton_task_id(plan, 0)) is None

    async def test_a_missing_objective_task_mints_nothing(self) -> None:
        plan = _plan()
        service, backend, dispatched = await _seed(plan, objective_missing=True)

        await _fire(service, plan)

        assert dispatched.await_count == 0
        assert await backend.tasks.get(skeleton_task_id(plan, 0)) is None

    async def test_a_foreign_task_on_the_derived_id_is_never_driven(self) -> None:
        """The id is derived, so nothing stops another writer occupying it.

        Handing that row to the pipeline would run somebody else's work under
        this plan's contract job and read its outcome as the contract's.
        """
        plan = _plan()
        foreign = Task(
            id=UUID(skeleton_task_id(plan, 0)),
            title="Something else entirely",
            description="Not a contract",
            type=TaskType.DEVELOPMENT,
            priority=Priority.LOW,
            project=sid(_PROJECT),
            created_by="somebody-else",
            status=TaskStatus.CREATED,
        )
        service, _, dispatched = await _seed(plan, foreign)

        await _fire(service, plan)

        assert dispatched.await_count == 0


class TestReadingWhereItGotTo:
    """What the rollup acts on."""

    async def test_no_attempt_yet_reads_absent(self) -> None:
        plan = _plan()
        backend = FakePersistenceBackend()
        await backend.plans.save(plan)

        state = await read_skeleton_state(backend, plan, allow_new_attempt=False)

        assert state.outcome is StageOutcome.ABSENT
        assert state.attempt == 0

    async def test_a_completed_job_reads_passed(self) -> None:
        """Reviewed, not merely written: COMPLETED is past the gate chain."""
        plan = _plan()
        service, backend, _ = await _seed(plan)
        await _fire(service, plan)
        minted = await backend.tasks.get(skeleton_task_id(plan, 0))
        assert minted is not None
        await backend.tasks.save(
            minted.model_copy(update={"status": TaskStatus.COMPLETED})
        )

        state = await read_skeleton_state(backend, plan, allow_new_attempt=False)

        assert state.outcome is StageOutcome.PASSED

    async def test_a_foreign_task_on_the_derived_id_reads_as_a_failed_attempt(
        self,
    ) -> None:
        """It is emphatically not evidence that the stage's work was done.

        The rollup routes on this read, so anything but FAILED here skips the
        contract stage on a row the stage never minted. Asserted on the read
        rather than only on "it was never dispatched", because a stage that
        declines to dispatch and reads as ABSENT is re-fired for ever, and one
        that reads as PASSED lets the units build against nothing.
        """
        plan = _plan()
        _service, backend, _ = await _seed(plan)
        await backend.tasks.save(
            Task(
                id=skeleton_task_uuid(plan, 0),
                title="Something else entirely",
                description="a row this stage never minted",
                type=TaskType.DEVELOPMENT,
                project=NotBlankStr(str(plan.project)),
                created_by=NotBlankStr("somebody-else"),
                plan_id=plan.id,
            )
        )

        state = await read_skeleton_state(backend, plan, allow_new_attempt=False)

        assert state.outcome is StageOutcome.FAILED

    async def test_a_failed_contract_stays_failed_however_it_is_read(self) -> None:
        """The head stage gets one attempt, so nothing is left to step over.

        A failed contract is a statement about the plan, and the answer is a
        replan, which is a new plan with its own first attempt. Nothing routes
        back into SKELETON for the plan that failed, so a larger ceiling here
        would describe attempts no pass could reach: the rollup only steps over
        a spent attempt when it observed the plan re-entering the stage, and a
        head status is never re-entered.

        Read both ways deliberately. The flag is the shared machinery's, and it
        must not turn a definite failure into a fresh attempt on a stage that
        has none left.
        """
        assert MAX_SKELETON_ATTEMPTS == 1
        plan = _plan()
        service, backend, _ = await _seed(plan)
        await _fire(service, plan)
        minted = await backend.tasks.get(skeleton_task_id(plan, 0))
        assert minted is not None
        await backend.tasks.save(
            minted.model_copy(update={"status": TaskStatus.FAILED})
        )

        held = await read_skeleton_state(backend, plan, allow_new_attempt=False)
        stepped = await read_skeleton_state(backend, plan, allow_new_attempt=True)

        assert held.outcome is StageOutcome.FAILED
        assert held.attempt == 0
        assert stepped.outcome is StageOutcome.FAILED
        assert stepped.attempt == 0
