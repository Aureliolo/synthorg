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
from synthorg.engine.initiative.ports import PlanStatusWriter
from synthorg.engine.initiative.rollup import _TASK_PAGE_SIZE, ProjectRollupService
from synthorg.engine.task_engine_models import TaskStateChanged
from synthorg.persistence.plan_protocol import PlanRepository
from synthorg.persistence.protocol import PersistenceBackend
from tests._shared import FakeClock, as_uuid, mock_of, sid
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


async def _seed(
    plan: Plan,
    *tasks: Task,
    project_status: ProjectStatus = ProjectStatus.ACTIVE,
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
    )
    return service, backend


async def _statuses(
    backend: FakePersistenceBackend,
) -> tuple[PlanStatus, ProjectStatus]:
    plan = await backend.plans.get(NotBlankStr(sid(_PLAN_ID)))
    project = await backend.projects.get(NotBlankStr(sid(_PROJECT)))
    assert plan is not None
    assert project is not None
    return plan.status, project.status


class TestCompletion:
    """A plan and its project complete only when every item genuinely passed."""

    async def test_all_work_completed_completes_plan_and_project(self) -> None:
        service, backend = await _seed(
            _plan(_item(_ITEM_A), _item(_ITEM_B)),
            _task(_ITEM_A, TaskStatus.COMPLETED),
            _task(_ITEM_B, TaskStatus.COMPLETED),
        )

        await service.recompute(as_uuid(_PLAN_ID))

        assert await _statuses(backend) == (
            PlanStatus.COMPLETED,
            ProjectStatus.COMPLETED,
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
            PlanStatus.COMPLETED,
            ProjectStatus.COMPLETED,
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
        assert plan.status is PlanStatus.COMPLETED
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
            PlanStatus.COMPLETED,
            ProjectStatus.COMPLETED,
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
        assert plan_status is PlanStatus.COMPLETED
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
        assert plan_status is PlanStatus.COMPLETED

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
            _item(sid(f"page-item-{index}")) for index in range(_TASK_PAGE_SIZE + 5)
        ]
        service, backend = await _seed(_plan(*items))
        for item in items:
            await backend.tasks.save(_task(item.id, TaskStatus.COMPLETED))

        await service.recompute(as_uuid(_PLAN_ID))

        assert await _statuses(backend) == (
            PlanStatus.COMPLETED,
            ProjectStatus.COMPLETED,
        )


class TestProjectBehindItsPlan:
    """A project several hops behind its plan walks, it does not jump."""

    async def test_planning_project_walks_through_active_to_completed(self) -> None:
        """PLANNING -> COMPLETED is not a legal transition.

        The project can be left PLANNING if the dispatch-time link write lost,
        so the rollup must reach COMPLETED via ACTIVE rather than writing a
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
        assert project.status is ProjectStatus.COMPLETED
        # Two hops, two writes: PLANNING -> ACTIVE -> COMPLETED.
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
