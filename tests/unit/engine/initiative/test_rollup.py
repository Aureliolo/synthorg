"""Tests for the initiative rollup service."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import UUID

import pytest

from synthorg.api.services.plan_service import PlanService
from synthorg.core.domain_errors import ConflictError
from synthorg.core.plan import Plan, PlanItem, PlanOption
from synthorg.core.plan_enums import PlanItemKind, PlanStatus
from synthorg.core.project import Project
from synthorg.core.project_enums import ProjectStatus
from synthorg.core.task import Task
from synthorg.core.task_enums import Priority, TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.initiative.completion import StallReason
from synthorg.engine.initiative.item_progress import TASK_PAGE_SIZE
from synthorg.engine.initiative.ports import (
    IntegrationPort,
    PlanStatusWriter,
    ReplanTriggerPort,
    RetroCapturePort,
)
from synthorg.engine.initiative.rollup import ProjectRollupService
from synthorg.engine.initiative.tail_stages import integration_task_id
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import TaskMutationResult, TaskStateChanged
from synthorg.persistence.plan_protocol import PlanRepository
from synthorg.persistence.protocol import PersistenceBackend
from tests._shared import (
    FakeClock,
    as_uuid,
    mock_of,
    sid,
)
from tests._shared import (
    RecordingReplanTrigger as _RecordingReplanTrigger,
)
from tests.unit.api.fakes_backend import FakePersistenceBackend

pytestmark = pytest.mark.unit

_PLAN_ID = "plan-1"
_PROJECT = "proj-1"
# Plan item ids must already be canonical UUID strings: ``subtask_uuid`` is
# identity on those, so the item id and its task's id stay the same value.
_ITEM_A = sid("item-a")
_ITEM_B = sid("item-b")
_DECISION = sid("item-decision")


def _item(
    item_id: str, *, kind: PlanItemKind = PlanItemKind.WORK, chosen: str | None = None
) -> PlanItem:
    options = (
        (
            PlanOption(id="opt-a", title="A", summary="first", recommended=True),
            PlanOption(id="opt-b", title="B", summary="second"),
        )
        if kind is PlanItemKind.DECISION
        else ()
    )
    return PlanItem(
        id=NotBlankStr(item_id),
        title=NotBlankStr(f"Item {item_id[:4]}"),
        description=NotBlankStr("Do the thing"),
        acceptance_criteria=(NotBlankStr("it is done"),),
        expected_artifacts=(
            () if kind is PlanItemKind.DECISION else (NotBlankStr("src/thing.py"),)
        ),
        kind=kind,
        options=options,
        chosen_option_id=chosen,
    )


def _plan(*items: PlanItem, status: PlanStatus = PlanStatus.EXECUTING) -> Plan:
    now = datetime(2026, 7, 19, tzinfo=UTC)
    return Plan(
        id=as_uuid(_PLAN_ID),
        project=NotBlankStr(sid(_PROJECT)),
        objective_id=NotBlankStr("obj-1"),
        objective_title=NotBlankStr("Ship it"),
        parent_task_id=NotBlankStr(sid("parent-1")),
        items=items,
        status=status,
        created_at=now,
        updated_at=now,
    )


def _task(item_id: str, status: TaskStatus) -> Task:
    return Task(
        id=UUID(item_id),
        title="Child",
        description="Child work",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project=sid(_PROJECT),
        plan_id=as_uuid(_PLAN_ID),
        plan_item_id=UUID(item_id),
        created_by="manager",
        assigned_to=sid("agent-1") if status is not TaskStatus.CREATED else None,
        status=status,
    )


async def _seed(  # noqa: PLR0913 -- keyword-only collaborator injection
    plan: Plan,
    *tasks: Task,
    project_status: ProjectStatus = ProjectStatus.ACTIVE,
    ship_retro_capture: RetroCapturePort | None = None,
    replan_trigger: ReplanTriggerPort | None = None,
    integration: IntegrationPort | None = None,
    task_engine: TaskEngine | None = None,
) -> tuple[ProjectRollupService, FakePersistenceBackend]:
    backend = FakePersistenceBackend()
    await backend.plans.save(plan)
    await backend.projects.save(
        Project(
            id=as_uuid(_PROJECT),
            name=NotBlankStr("Initiative"),
            plan_id=as_uuid(_PLAN_ID),
            status=project_status,
        )
    )
    for task in tasks:
        await backend.tasks.save(task)
    clock = FakeClock()
    service = ProjectRollupService(
        persistence=backend,
        plan_status_writer=PlanService(repo=backend.plans, clock=clock),
        clock=clock,
        task_engine=task_engine,
        ship_retro_capture=ship_retro_capture,
        replan_trigger=replan_trigger,
        integration=integration,
    )
    return service, backend


class _RecordingRetroCapture:
    """A retro-capture port that records the projects it was fired for."""

    def __init__(self) -> None:
        self.fired: list[str] = []
        self.drained: list[float] = []

    def schedule(self, *, plan: Plan, project: Project) -> None:
        del plan
        self.fired.append(str(project.id))

    async def drain(self, *, timeout_sec: float) -> None:
        self.drained.append(timeout_sec)


async def _statuses(
    backend: FakePersistenceBackend,
) -> tuple[PlanStatus, ProjectStatus]:
    plan = await backend.plans.get(NotBlankStr(sid(_PLAN_ID)))
    project = await backend.projects.get(NotBlankStr(sid(_PROJECT)))
    assert plan is not None
    assert project is not None
    return plan.status, project.status


class TestCompletion:
    """A plan opens its tail only when every item genuinely passed."""

    async def test_all_work_completed_opens_the_tail(self) -> None:
        """Verified pieces are not a delivered whole, so the tail opens."""
        service, backend = await _seed(
            _plan(_item(_ITEM_A), _item(_ITEM_B)),
            _task(_ITEM_A, TaskStatus.COMPLETED),
            _task(_ITEM_B, TaskStatus.COMPLETED),
        )

        await service.recompute(as_uuid(_PLAN_ID))

        assert await _statuses(backend) == (
            PlanStatus.INTEGRATING,
            ProjectStatus.INTEGRATING,
        )

    async def test_unverified_task_cannot_complete_the_project(self) -> None:
        """The core invariant of this change.

        A task in IN_REVIEW has executed but has not passed the completion
        oracle. The rollup reads persisted task status, so it sees the task as
        not done and the initiative stays in flight.
        """
        service, backend = await _seed(
            _plan(_item(_ITEM_A), _item(_ITEM_B)),
            _task(_ITEM_A, TaskStatus.COMPLETED),
            _task(_ITEM_B, TaskStatus.IN_REVIEW),
        )

        await service.recompute(as_uuid(_PLAN_ID))

        assert await _statuses(backend) == (
            PlanStatus.EXECUTING,
            ProjectStatus.ACTIVE,
        )

    async def test_failed_task_leaves_the_project_active(self) -> None:
        """Failure is a derived count, never a lifecycle state."""
        service, backend = await _seed(
            _plan(_item(_ITEM_A), _item(_ITEM_B)),
            _task(_ITEM_A, TaskStatus.COMPLETED),
            _task(_ITEM_B, TaskStatus.FAILED),
        )

        await service.recompute(as_uuid(_PLAN_ID))

        assert await _statuses(backend) == (
            PlanStatus.EXECUTING,
            ProjectStatus.ACTIVE,
        )

    async def test_unresolved_decision_blocks_completion(self) -> None:
        service, backend = await _seed(
            _plan(_item(_ITEM_A), _item(_DECISION, kind=PlanItemKind.DECISION)),
            _task(_ITEM_A, TaskStatus.COMPLETED),
        )

        await service.recompute(as_uuid(_PLAN_ID))

        assert await _statuses(backend) == (
            PlanStatus.EXECUTING,
            ProjectStatus.ACTIVE,
        )

    async def test_resolved_decision_counts_as_done(self) -> None:
        service, backend = await _seed(
            _plan(
                _item(_ITEM_A),
                _item(_DECISION, kind=PlanItemKind.DECISION, chosen="opt-a"),
            ),
            _task(_ITEM_A, TaskStatus.COMPLETED),
        )

        await service.recompute(as_uuid(_PLAN_ID))

        assert await _statuses(backend) == (
            PlanStatus.INTEGRATING,
            ProjectStatus.INTEGRATING,
        )

    async def test_missing_task_for_an_item_blocks_completion(self) -> None:
        """An item whose task was never dispatched is not silently done."""
        service, backend = await _seed(
            _plan(_item(_ITEM_A), _item(_ITEM_B)),
            _task(_ITEM_A, TaskStatus.COMPLETED),
        )

        await service.recompute(as_uuid(_PLAN_ID))

        assert await _statuses(backend) == (
            PlanStatus.EXECUTING,
            ProjectStatus.ACTIVE,
        )


class TestParentTaskAdvance:
    """The objective task lands its status on the same recompute."""

    @staticmethod
    def _engine(parent: Task) -> tuple[TaskEngine, AsyncMock]:
        """Build the engine double plus a handle on its submit mock.

        Returns:
            The typed engine and the ``submit`` mock the assertions read.
        """
        submit = AsyncMock(
            return_value=TaskMutationResult(request_id="r", success=True, version=1),
        )
        engine: TaskEngine = mock_of[TaskEngine](
            get_task=AsyncMock(return_value=parent),
            submit=submit,
        )
        return engine, submit

    @staticmethod
    def _parent(status: TaskStatus) -> Task:
        return Task(
            id=as_uuid("parent-1"),
            title="Objective",
            description="Ship it",
            type=TaskType.DEVELOPMENT,
            priority=Priority.MEDIUM,
            project=sid(_PROJECT),
            created_by="ceo",
            assigned_to=sid("coordinator"),
            status=status,
        )

    async def test_a_completed_plan_walks_the_parent_to_completed(self) -> None:
        """Coordination leaves the parent IN_PROGRESS; the rollup finishes it.

        Coordination advances the parent once, when its children are still
        awaiting the review gate, so the terminal hop can only be made later
        by a derivation that reads persisted status.
        """
        engine, submit = self._engine(self._parent(TaskStatus.IN_PROGRESS))
        service, _ = await _seed(
            _plan(_item(_ITEM_A), _item(_ITEM_B), status=PlanStatus.COMPLETED),
            _task(_ITEM_A, TaskStatus.COMPLETED),
            _task(_ITEM_B, TaskStatus.COMPLETED),
            task_engine=engine,
        )

        await service.recompute(as_uuid(_PLAN_ID))

        targets = [call.args[0].target_status for call in submit.await_args_list]
        assert targets[-1] is TaskStatus.COMPLETED

    async def test_the_tail_holds_the_objective_task_open(self) -> None:
        """Every item passing its own gate does not deliver the objective.

        The plan is only INTEGRATING here, so completing the task standing for
        the whole initiative would show delivered work that no one has yet
        assembled or scored.
        """
        engine, submit = self._engine(self._parent(TaskStatus.IN_PROGRESS))
        service, _ = await _seed(
            _plan(_item(_ITEM_A), _item(_ITEM_B)),
            _task(_ITEM_A, TaskStatus.COMPLETED),
            _task(_ITEM_B, TaskStatus.COMPLETED),
            task_engine=engine,
        )

        await service.recompute(as_uuid(_PLAN_ID))

        targets = [call.args[0].target_status for call in submit.await_args_list]
        assert TaskStatus.COMPLETED not in targets

    async def test_unverified_child_leaves_the_parent_open(self) -> None:
        """A child still in review cannot complete the objective task."""
        engine, submit = self._engine(self._parent(TaskStatus.IN_PROGRESS))
        service, _ = await _seed(
            _plan(_item(_ITEM_A), _item(_ITEM_B)),
            _task(_ITEM_A, TaskStatus.COMPLETED),
            _task(_ITEM_B, TaskStatus.IN_REVIEW),
            task_engine=engine,
        )

        await service.recompute(as_uuid(_PLAN_ID))

        targets = [call.args[0].target_status for call in submit.await_args_list]
        assert TaskStatus.COMPLETED not in targets

    async def test_no_task_engine_skips_the_parent_walk(self) -> None:
        """An unwired engine leaves the plan and project rollup untouched."""
        service, backend = await _seed(
            _plan(_item(_ITEM_A)),
            _task(_ITEM_A, TaskStatus.COMPLETED),
        )

        await service.recompute(as_uuid(_PLAN_ID))

        assert await _statuses(backend) == (
            PlanStatus.INTEGRATING,
            ProjectStatus.INTEGRATING,
        )


class TestIdempotence:
    """Recompute is the mechanism that makes best-effort delivery safe."""

    async def test_repeated_recompute_is_stable(self) -> None:
        service, backend = await _seed(
            _plan(_item(_ITEM_A)),
            _task(_ITEM_A, TaskStatus.COMPLETED),
        )

        for _ in range(3):
            await service.recompute(as_uuid(_PLAN_ID))

        plan = await backend.plans.get(NotBlankStr(sid(_PLAN_ID)))
        assert plan is not None
        assert plan.status is PlanStatus.INTEGRATING
        # One status write, not one per recompute.
        assert plan.version == 2

    async def test_a_dropped_event_heals_on_the_next_one(self) -> None:
        """Drift from a missed event is repaired without a reconciler."""
        service, backend = await _seed(
            _plan(_item(_ITEM_A), _item(_ITEM_B)),
            _task(_ITEM_A, TaskStatus.COMPLETED),
            _task(_ITEM_B, TaskStatus.IN_PROGRESS),
        )
        # The event for item B completing never arrives; the task lands anyway.
        await backend.tasks.save(_task(_ITEM_B, TaskStatus.COMPLETED))

        # A later, unrelated event triggers a recompute of the same plan.
        await service.recompute(as_uuid(_PLAN_ID))

        assert await _statuses(backend) == (
            PlanStatus.INTEGRATING,
            ProjectStatus.INTEGRATING,
        )


class TestGuards:
    """Statuses the rollup must not move."""

    async def test_on_hold_project_is_not_completed(self) -> None:
        service, backend = await _seed(
            _plan(_item(_ITEM_A)),
            _task(_ITEM_A, TaskStatus.COMPLETED),
            project_status=ProjectStatus.ON_HOLD,
        )

        await service.recompute(as_uuid(_PLAN_ID))

        plan_status, project_status = await _statuses(backend)
        assert plan_status is PlanStatus.INTEGRATING
        assert project_status is ProjectStatus.ON_HOLD

    async def test_cancelled_project_is_left_alone(self) -> None:
        service, backend = await _seed(
            _plan(_item(_ITEM_A)),
            _task(_ITEM_A, TaskStatus.COMPLETED),
            project_status=ProjectStatus.CANCELLED,
        )

        await service.recompute(as_uuid(_PLAN_ID))

        _, project_status = await _statuses(backend)
        assert project_status is ProjectStatus.CANCELLED

    async def test_terminal_plan_is_skipped(self) -> None:
        service, backend = await _seed(
            _plan(_item(_ITEM_A), status=PlanStatus.SUPERSEDED),
            _task(_ITEM_A, TaskStatus.COMPLETED),
        )

        await service.recompute(as_uuid(_PLAN_ID))

        plan_status, _ = await _statuses(backend)
        assert plan_status is PlanStatus.SUPERSEDED

    async def test_missing_plan_is_a_no_op(self) -> None:
        service, backend = await _seed(
            _plan(_item(_ITEM_A)),
            _task(_ITEM_A, TaskStatus.COMPLETED),
        )

        await service.recompute(as_uuid("plan-absent"))

        # No-op means the seeded initiative is untouched, not merely that the
        # call did not raise.
        assert await _statuses(backend) == (
            PlanStatus.EXECUTING,
            ProjectStatus.ACTIVE,
        )


class TestObserver:
    """The TaskEngine observer contract: trigger only, never raise."""

    async def test_event_for_a_plan_task_rolls_up(self) -> None:
        service, backend = await _seed(
            _plan(_item(_ITEM_A)),
            _task(_ITEM_A, TaskStatus.COMPLETED),
        )

        await service.on_task_state_changed(
            _event(_task(_ITEM_A, TaskStatus.COMPLETED))
        )

        plan_status, _ = await _statuses(backend)
        assert plan_status is PlanStatus.INTEGRATING

    async def test_event_for_a_task_without_a_plan_is_ignored(self) -> None:
        service, backend = await _seed(
            _plan(_item(_ITEM_A)),
            _task(_ITEM_A, TaskStatus.COMPLETED),
        )
        unlinked = _task(_ITEM_A, TaskStatus.COMPLETED).model_copy(
            update={"plan_id": None, "plan_item_id": None}
        )

        await service.on_task_state_changed(_event(unlinked))

        plan_status, _ = await _statuses(backend)
        assert plan_status is PlanStatus.EXECUTING

    async def test_a_repository_failure_never_escapes_the_observer(self) -> None:
        """A rollup failure must not stall the engine's task processing."""
        plans = mock_of[PlanRepository](
            get=AsyncMock(side_effect=RuntimeError("plans unavailable"))
        )
        service = ProjectRollupService(
            persistence=mock_of[PersistenceBackend](plans=plans),
            plan_status_writer=mock_of[PlanStatusWriter](),
            clock=FakeClock(),
        )

        await service.on_task_state_changed(
            _event(_task(_ITEM_A, TaskStatus.COMPLETED))
        )

        # The failure was reached and swallowed, not skipped before the read.
        plans.get.assert_awaited()


class TestPlanIsolation:
    """A rollup sees only its own plan's work."""

    async def test_another_plans_tasks_do_not_complete_this_plan(self) -> None:
        """Cross-plan bleed would complete an initiative off unrelated work.

        Every other test in this file seeds a single plan, so a task query that
        ignored its plan filter would be indistinguishable from a correct one.
        """
        service, backend = await _seed(
            _plan(_item(_ITEM_A), _item(_ITEM_B)),
            _task(_ITEM_A, TaskStatus.COMPLETED),
        )
        # Item B's work belongs to a different plan entirely.
        other = _task(_ITEM_B, TaskStatus.COMPLETED).model_copy(
            update={"plan_id": as_uuid("plan-other")}
        )
        await backend.tasks.save(other)

        await service.recompute(as_uuid(_PLAN_ID))

        assert await _statuses(backend) == (
            PlanStatus.EXECUTING,
            ProjectStatus.ACTIVE,
        )

    async def test_a_full_page_of_tasks_drains_to_the_next_page(self) -> None:
        """The paging loop's second iteration is otherwise never executed."""
        items = [
            _item(sid(f"page-item-{index}")) for index in range(TASK_PAGE_SIZE + 5)
        ]
        service, backend = await _seed(_plan(*items))
        for item in items:
            await backend.tasks.save(_task(item.id, TaskStatus.COMPLETED))

        await service.recompute(as_uuid(_PLAN_ID))

        assert await _statuses(backend) == (
            PlanStatus.INTEGRATING,
            ProjectStatus.INTEGRATING,
        )


class TestProjectBehindItsPlan:
    """A project several hops behind its plan walks, it does not jump."""

    async def test_planning_project_walks_through_active(self) -> None:
        """PLANNING -> INTEGRATING is not a legal transition.

        The project can be left PLANNING if the dispatch-time link write lost,
        so the rollup must reach the tail via ACTIVE rather than writing a
        status the state machine rejects.
        """
        service, backend = await _seed(
            _plan(_item(_ITEM_A)),
            _task(_ITEM_A, TaskStatus.COMPLETED),
            project_status=ProjectStatus.PLANNING,
        )

        await service.recompute(as_uuid(_PLAN_ID))

        project = await backend.projects.get(NotBlankStr(sid(_PROJECT)))
        assert project is not None
        assert project.status is ProjectStatus.INTEGRATING
        # Two hops, two writes: PLANNING -> ACTIVE -> INTEGRATING.
        assert project.version == 3

    async def test_a_failed_plan_write_still_reconciles_the_project(self) -> None:
        """A plan-side failure must not strand a project behind its plan."""
        plans = mock_of[PlanStatusWriter](
            sync_status=AsyncMock(side_effect=ConflictError("refused"))
        )
        backend = FakePersistenceBackend()
        await backend.plans.save(_plan(_item(_ITEM_A)))
        await backend.projects.save(
            Project(
                id=as_uuid(_PROJECT),
                name=NotBlankStr("Initiative"),
                plan_id=as_uuid(_PLAN_ID),
                status=ProjectStatus.PLANNING,
            )
        )
        await backend.tasks.save(_task(_ITEM_A, TaskStatus.COMPLETED))
        service = ProjectRollupService(
            persistence=backend,
            plan_status_writer=plans,
            clock=FakeClock(),
        )

        await service.recompute(as_uuid(_PLAN_ID))

        # The plan write was refused, but the project still catches up to the
        # plan's current (EXECUTING) status rather than staying PLANNING.
        project = await backend.projects.get(NotBlankStr(sid(_PROJECT)))
        assert project is not None
        assert project.status is ProjectStatus.ACTIVE

    async def test_an_approved_plan_opens_the_tail_through_executing(self) -> None:
        """APPROVED -> INTEGRATING is not a legal hop.

        Dispatch normally moves the plan to EXECUTING first, but that write is
        a CAS whose exhaustion is swallowed, so a plan can be APPROVED while
        its tasks run. Opening its tail must walk through EXECUTING rather than
        jump, or the plan stalls one hop short.
        """
        service, backend = await _seed(
            _plan(_item(_ITEM_A), status=PlanStatus.APPROVED),
            _task(_ITEM_A, TaskStatus.COMPLETED),
        )

        await service.recompute(as_uuid(_PLAN_ID))

        assert await _statuses(backend) == (
            PlanStatus.INTEGRATING,
            ProjectStatus.INTEGRATING,
        )

    async def test_terminal_plan_still_reconciles_its_project(self) -> None:
        """The project write must not be gated on the plan being non-terminal.

        A project write can fail on the same event that completes the plan.
        If a terminal plan short-circuited the whole recompute, no later event
        could ever repair the project.
        """
        service, backend = await _seed(
            _plan(_item(_ITEM_A), status=PlanStatus.COMPLETED),
            _task(_ITEM_A, TaskStatus.COMPLETED),
        )

        await service.recompute(as_uuid(_PLAN_ID))

        assert await _statuses(backend) == (
            PlanStatus.COMPLETED,
            ProjectStatus.COMPLETED,
        )


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
    """Build the plan's integration task, which implements no plan item."""
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
        # to EXECUTING; it must not.
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


class TestReplanTrigger:
    """A plan that can no longer advance replans instead of hanging."""

    async def test_fires_when_no_item_can_advance(self) -> None:
        trigger = _RecordingReplanTrigger()
        service, _ = await _seed(
            _plan(_item(_ITEM_A), _item(_ITEM_B)),
            _task(_ITEM_A, TaskStatus.COMPLETED),
            _task(_ITEM_B, TaskStatus.FAILED),
            replan_trigger=trigger,
        )

        await service.recompute(as_uuid(_PLAN_ID))

        assert trigger.fired == [(sid(_PLAN_ID), StallReason.ALL_FAILED)]

    async def test_does_not_fire_while_work_is_in_flight(self) -> None:
        trigger = _RecordingReplanTrigger()
        service, _ = await _seed(
            _plan(_item(_ITEM_A), _item(_ITEM_B)),
            _task(_ITEM_A, TaskStatus.FAILED),
            _task(_ITEM_B, TaskStatus.IN_PROGRESS),
            replan_trigger=trigger,
        )

        await service.recompute(as_uuid(_PLAN_ID))

        assert trigger.fired == []

    async def test_does_not_fire_for_a_terminal_plan(self) -> None:
        """A superseded plan's dead items are the retired revision's, not live."""
        trigger = _RecordingReplanTrigger()
        service, _ = await _seed(
            _plan(_item(_ITEM_A), status=PlanStatus.SUPERSEDED),
            _task(_ITEM_A, TaskStatus.FAILED),
            replan_trigger=trigger,
        )

        await service.recompute(as_uuid(_PLAN_ID))

        assert trigger.fired == []

    async def test_drain_delegates_to_the_trigger(self) -> None:
        trigger = _RecordingReplanTrigger()
        service, _ = await _seed(
            _plan(_item(_ITEM_A)),
            _task(_ITEM_A, TaskStatus.FAILED),
            replan_trigger=trigger,
        )

        await service.drain_replan_trigger(timeout_sec=5.0)

        assert trigger.drained == [5.0]

    async def test_drain_is_a_noop_without_a_wired_trigger(self) -> None:
        service, _ = await _seed(
            _plan(_item(_ITEM_A)),
            _task(_ITEM_A, TaskStatus.FAILED),
        )

        await service.drain_replan_trigger(timeout_sec=5.0)


class TestRetroTrigger:
    """The retrospective fires exactly once, on the edge into COMPLETED."""

    async def test_fires_when_the_project_first_completes(self) -> None:
        """The evaluate stage completed the plan; the project catches up."""
        retro = _RecordingRetroCapture()
        service, _ = await _seed(
            _plan(_item(_ITEM_A), _item(_ITEM_B), status=PlanStatus.COMPLETED),
            _task(_ITEM_A, TaskStatus.COMPLETED),
            _task(_ITEM_B, TaskStatus.COMPLETED),
            ship_retro_capture=retro,
        )

        await service.recompute(as_uuid(_PLAN_ID))

        assert retro.fired == [sid(_PROJECT)]

    async def test_does_not_fire_while_work_is_in_flight(self) -> None:
        retro = _RecordingRetroCapture()
        service, _ = await _seed(
            _plan(_item(_ITEM_A), _item(_ITEM_B)),
            _task(_ITEM_A, TaskStatus.COMPLETED),
            _task(_ITEM_B, TaskStatus.IN_REVIEW),
            ship_retro_capture=retro,
        )

        await service.recompute(as_uuid(_PLAN_ID))

        assert retro.fired == []

    async def test_does_not_refire_for_an_already_completed_project(self) -> None:
        """A recompute over a terminal project must not re-trigger the retro."""
        retro = _RecordingRetroCapture()
        service, _ = await _seed(
            _plan(_item(_ITEM_A), _item(_ITEM_B), status=PlanStatus.COMPLETED),
            _task(_ITEM_A, TaskStatus.COMPLETED),
            _task(_ITEM_B, TaskStatus.COMPLETED),
            project_status=ProjectStatus.COMPLETED,
            ship_retro_capture=retro,
        )

        await service.recompute(as_uuid(_PLAN_ID))

        assert retro.fired == []

    async def test_does_not_fire_when_the_project_write_was_refused(self) -> None:
        """A refused project write (project is None) must not trigger a retro."""
        retro = _RecordingRetroCapture()
        service, _ = await _seed(
            _plan(_item(_ITEM_A)),
            _task(_ITEM_A, TaskStatus.COMPLETED),
            ship_retro_capture=retro,
        )

        service._maybe_capture_retro(
            _plan(_item(_ITEM_A)), None, before=ProjectStatus.ACTIVE
        )

        assert retro.fired == []

    async def test_drain_delegates_to_the_capture_tail(self) -> None:
        retro = _RecordingRetroCapture()
        service, _ = await _seed(
            _plan(_item(_ITEM_A)),
            _task(_ITEM_A, TaskStatus.COMPLETED),
            ship_retro_capture=retro,
        )

        await service.drain_retro_capture(timeout_sec=5.0)

        assert retro.drained == [5.0]

    async def test_drain_is_a_noop_without_a_wired_tail(self) -> None:
        service, _ = await _seed(
            _plan(_item(_ITEM_A)),
            _task(_ITEM_A, TaskStatus.COMPLETED),
        )

        await service.drain_retro_capture(timeout_sec=5.0)


def _event(task: Task) -> TaskStateChanged:
    return TaskStateChanged(
        mutation_type="transition",
        request_id=NotBlankStr("req-1"),
        requested_by=NotBlankStr("engine"),
        task_id=NotBlankStr(str(task.id)),
        task=task,
        previous_status=TaskStatus.IN_REVIEW,
        new_status=task.status,
        version=2,
        timestamp=datetime(2026, 7, 19, tzinfo=UTC),
    )
