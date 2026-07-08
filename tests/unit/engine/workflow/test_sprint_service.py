"""Unit tests for the SprintService orchestration."""

from typing import Any, override
from unittest.mock import AsyncMock

import pytest

from synthorg.core.task import Task
from synthorg.core.task_enums import Complexity, Priority, TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import (
    SprintBacklogFullError,
    SprintNotFoundError,
    SprintTransitionConflictError,
)
from synthorg.engine.task_engine_models import TaskStateChanged
from synthorg.engine.workflow.ceremony_scheduler import CeremonyScheduler
from synthorg.engine.workflow.enums import WorkflowType
from synthorg.engine.workflow.sprint_config import SprintConfig
from synthorg.engine.workflow.sprint_lifecycle import Sprint, SprintStatus
from synthorg.engine.workflow.sprint_service import SprintService
from synthorg.persistence.sprint_protocol import SprintFilterSpec
from synthorg.persistence.task_protocol import TaskRepository
from synthorg.settings.resolver_protocol import ConfigResolverProtocol
from tests._shared import FakeClock, as_uuid, mock_of

pytestmark = pytest.mark.unit

#: Configured mock, typed loosely for the unittest.mock API.
_Configured = Any  # type: ignore[explicit-any]


class _FakeSprintRepo:
    """In-memory ``SprintRepository`` for driving the service end-to-end."""

    def __init__(self) -> None:
        self._rows: dict[str, Sprint] = {}

    async def save(self, entity: Sprint) -> None:
        self._rows[entity.id] = entity

    async def get(self, entity_id: str) -> Sprint | None:
        return self._rows.get(entity_id)

    async def delete(self, entity_id: str) -> bool:
        return self._rows.pop(entity_id, None) is not None

    def _sorted(self, rows: list[Sprint]) -> list[Sprint]:
        return sorted(rows, key=lambda s: (s.sprint_number, s.id), reverse=True)

    async def list_items(
        self, *, limit: int = 50, offset: int = 0
    ) -> tuple[Sprint, ...]:
        rows = self._sorted(list(self._rows.values()))
        return tuple(rows[offset : offset + limit])

    async def query(
        self, filter_spec: SprintFilterSpec, *, limit: int = 50, offset: int = 0
    ) -> tuple[Sprint, ...]:
        rows = [
            s
            for s in self._rows.values()
            if (filter_spec.project is None or s.project == filter_spec.project)
            and (filter_spec.status is None or s.status is filter_spec.status)
        ]
        rows = self._sorted(rows)
        return tuple(rows[offset : offset + limit])

    async def count(self, filter_spec: SprintFilterSpec) -> int:
        return len(await self.query(filter_spec, limit=1_000_000))

    async def transition_if(
        self,
        entity_id: str,
        from_state: SprintStatus,
        to_state: SprintStatus,
        **updates: object,
    ) -> bool:
        row = self._rows.get(entity_id)
        if row is None or row.status is not from_state:
            return False
        overrides = {k: v for k, v in updates.items() if v is not None}
        self._rows[entity_id] = row.model_copy(update={"status": to_state, **overrides})
        return True


def _status_aware_tasks(tasks: tuple[Task, ...]) -> _Configured:
    """Task repo mock whose ``query`` filters by the spec's project + status."""

    async def _query(
        filter_spec: object, *, limit: int = 50, offset: int = 0
    ) -> tuple[Task, ...]:
        project = getattr(filter_spec, "project", None)
        status = getattr(filter_spec, "status", None)
        rows = [
            t
            for t in tasks
            if (project is None or t.project == project)
            and (status is None or t.status is status)
        ]
        return tuple(rows[offset : offset + limit])

    return mock_of[TaskRepository](query=AsyncMock(side_effect=_query))


def _task(
    label: str,
    *,
    status: TaskStatus = TaskStatus.CREATED,
    project: str = "proj-1",
    complexity: Complexity = Complexity.SIMPLE,
) -> Task:
    requires_assignee = {
        TaskStatus.ASSIGNED,
        TaskStatus.IN_PROGRESS,
        TaskStatus.IN_REVIEW,
        TaskStatus.COMPLETED,
    }
    return Task(
        id=as_uuid(label),
        title=f"Task {label}",
        description=f"Description for {label}",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project=NotBlankStr(project),
        created_by="manager",
        estimated_complexity=complexity,
        assigned_to=str(as_uuid("agent-1")) if status in requires_assignee else None,
        status=status,
    )


def _event(task: Task, new_status: TaskStatus) -> TaskStateChanged:
    return TaskStateChanged(
        mutation_type="transition",
        request_id=NotBlankStr("req-1"),
        requested_by=NotBlankStr("manager"),
        task_id=NotBlankStr(str(task.id)),
        task=task,
        new_status=new_status,
        version=1,
    )


def _resolver(*, enabled: bool = True, agile: bool = True) -> _Configured:
    return mock_of[ConfigResolverProtocol](
        get_bool=AsyncMock(return_value=enabled),
        get_enum=AsyncMock(
            return_value=(WorkflowType.AGILE_KANBAN if agile else WorkflowType.KANBAN)
        ),
    )


def _scheduler(*, on_complete: object | None = None) -> _Configured:
    return mock_of[CeremonyScheduler](
        activate_sprint=AsyncMock(return_value=None),
        deactivate_sprint=AsyncMock(return_value=None),
        on_task_completed=AsyncMock(
            side_effect=on_complete or (lambda sprint, _t, _p: sprint)
        ),
    )


def _service(
    *,
    sprints: _Configured = None,
    tasks: _Configured = None,
    scheduler: _Configured = None,
    resolver: _Configured = None,
    sprint_config: SprintConfig | None = None,
) -> SprintService:
    task_repo = tasks or mock_of[TaskRepository](query=AsyncMock(return_value=()))
    return SprintService(
        sprint_repository=sprints or _FakeSprintRepo(),
        task_repository=task_repo,
        ceremony_scheduler=scheduler or _scheduler(),
        config_resolver=resolver or _resolver(),
        sprint_config=sprint_config,
        clock=FakeClock(),
    )


class TestExplicitControl:
    async def test_create_sprint_numbers_sequentially(self) -> None:
        repo = _FakeSprintRepo()
        service = _service(sprints=repo)
        first = await service.create_sprint("proj-1")
        second = await service.create_sprint("proj-1")
        assert first.sprint_number == 1
        assert second.sprint_number == 2
        assert first.status is SprintStatus.PLANNING

    async def test_add_task_appends_to_backlog(self) -> None:
        service = _service()
        sprint = await service.create_sprint("proj-1")
        updated = await service.add_task(sprint.id, "task-a", 3.0)
        assert updated.task_ids == ("task-a",)
        assert updated.story_points_committed == pytest.approx(3.0)

    async def test_add_task_rejects_full_backlog(self) -> None:
        service = _service(sprint_config=SprintConfig(max_tasks_per_sprint=1))
        sprint = await service.create_sprint("proj-1")
        await service.add_task(sprint.id, "task-a", 1.0)
        with pytest.raises(SprintBacklogFullError):
            await service.add_task(sprint.id, "task-b", 1.0)

    async def test_add_task_unknown_sprint_raises(self) -> None:
        service = _service()
        with pytest.raises(SprintNotFoundError):
            await service.add_task("missing", "task-a", 1.0)

    async def test_start_sprint_activates_scheduler(self) -> None:
        scheduler = _scheduler()
        service = _service(scheduler=scheduler)
        sprint = await service.create_sprint("proj-1")
        started = await service.start_sprint(sprint.id)
        assert started.status is SprintStatus.ACTIVE
        assert started.start_date is not None
        scheduler.activate_sprint.assert_awaited_once()

    async def test_start_sprint_conflict_when_not_planning(self) -> None:
        service = _service()
        sprint = await service.create_sprint("proj-1")
        await service.start_sprint(sprint.id)
        with pytest.raises(SprintTransitionConflictError):
            await service.start_sprint(sprint.id)

    async def test_add_task_conflict_when_not_planning(self) -> None:
        service = _service()
        sprint = await service.create_sprint("proj-1")
        await service.start_sprint(sprint.id)
        with pytest.raises(SprintTransitionConflictError):
            await service.add_task(sprint.id, "task-a", 1.0)

    async def test_advance_walks_linear_lifecycle(self) -> None:
        service = _service()
        sprint = await service.create_sprint("proj-1")
        active = await service.advance_sprint(sprint.id)
        assert active.status is SprintStatus.ACTIVE
        assert active.start_date is not None
        review = await service.advance_sprint(sprint.id)
        assert review.status is SprintStatus.IN_REVIEW

    async def test_advance_stamps_end_date_on_completion(self) -> None:
        service = _service()
        sprint = await service.create_sprint("proj-1")
        latest = sprint
        for _ in range(4):
            latest = await service.advance_sprint(sprint.id)
        assert latest.status is SprintStatus.COMPLETED
        assert latest.end_date is not None

    async def test_advance_terminal_raises(self) -> None:
        service = _service()
        sprint = await service.create_sprint("proj-1")
        for _ in range(4):
            await service.advance_sprint(sprint.id)
        with pytest.raises(SprintTransitionConflictError):
            await service.advance_sprint(sprint.id)


class TestObserver:
    async def test_disabled_flag_is_noop(self) -> None:
        repo = _FakeSprintRepo()
        service = _service(sprints=repo, resolver=_resolver(enabled=False))
        await service.on_task_state_changed(
            _event(_task("t1", status=TaskStatus.ASSIGNED), TaskStatus.ASSIGNED)
        )
        assert await repo.count(SprintFilterSpec()) == 0

    async def test_non_agile_workflow_is_noop(self) -> None:
        repo = _FakeSprintRepo()
        service = _service(sprints=repo, resolver=_resolver(agile=False))
        await service.on_task_state_changed(
            _event(_task("t1", status=TaskStatus.ASSIGNED), TaskStatus.ASSIGNED)
        )
        assert await repo.count(SprintFilterSpec()) == 0

    async def test_assigned_auto_creates_and_starts_sprint(self) -> None:
        repo = _FakeSprintRepo()
        trigger = _task("t1", status=TaskStatus.ASSIGNED)
        backlog = (trigger, _task("t2", status=TaskStatus.CREATED))
        tasks = mock_of[TaskRepository](query=AsyncMock(return_value=backlog))
        scheduler = _scheduler()
        service = _service(sprints=repo, tasks=tasks, scheduler=scheduler)

        await service.on_task_state_changed(_event(trigger, TaskStatus.ASSIGNED))
        await service.drain()

        sprints = await repo.query(SprintFilterSpec(project="proj-1"))
        assert len(sprints) == 1
        active = sprints[0]
        assert active.status is SprintStatus.ACTIVE
        assert set(active.task_ids) == {str(trigger.id), str(as_uuid("t2"))}
        scheduler.activate_sprint.assert_awaited_once()

    async def test_second_assigned_does_not_create_duplicate(self) -> None:
        repo = _FakeSprintRepo()
        trigger = _task("t1", status=TaskStatus.ASSIGNED)
        tasks = mock_of[TaskRepository](query=AsyncMock(return_value=(trigger,)))
        service = _service(sprints=repo, tasks=tasks)
        await service.on_task_state_changed(_event(trigger, TaskStatus.ASSIGNED))
        await service.on_task_state_changed(_event(trigger, TaskStatus.ASSIGNED))
        assert await repo.count(SprintFilterSpec(project="proj-1")) == 1

    async def test_auto_create_excludes_terminal_tasks(self) -> None:
        repo = _FakeSprintRepo()
        trigger = _task("t1", status=TaskStatus.ASSIGNED)
        done = _task("t2", status=TaskStatus.COMPLETED)
        tasks = _status_aware_tasks((trigger, done))
        service = _service(sprints=repo, tasks=tasks)

        await service.on_task_state_changed(_event(trigger, TaskStatus.ASSIGNED))
        await service.drain()

        sprints = await repo.query(SprintFilterSpec(project="proj-1"))
        assert len(sprints) == 1
        assert set(sprints[0].task_ids) == {str(trigger.id)}

    async def test_completion_marks_task_done(self) -> None:
        repo = _FakeSprintRepo()
        service = _service(sprints=repo)
        sprint = await service.create_sprint("proj-1")
        await service.add_task(sprint.id, str(as_uuid("t1")), 1.0)
        await service.start_sprint(sprint.id)

        task = _task("t1", status=TaskStatus.COMPLETED)
        await service.on_task_state_changed(_event(task, TaskStatus.COMPLETED))
        await service.drain()

        stored = await repo.get(sprint.id)
        assert stored is not None
        assert str(as_uuid("t1")) in stored.completed_task_ids

    async def test_completion_finalizes_when_backlog_delivered(self) -> None:
        repo = _FakeSprintRepo()

        def _to_in_review(sprint: Sprint, _t: str, _p: float) -> Sprint:
            return sprint.with_transition(SprintStatus.IN_REVIEW)

        scheduler = _scheduler(on_complete=_to_in_review)
        service = _service(sprints=repo, scheduler=scheduler)
        sprint = await service.create_sprint("proj-1")
        await service.add_task(sprint.id, str(as_uuid("t1")), 1.0)
        await service.start_sprint(sprint.id)

        task = _task("t1", status=TaskStatus.COMPLETED)
        await service.on_task_state_changed(_event(task, TaskStatus.COMPLETED))
        await service.drain()

        stored = await repo.get(sprint.id)
        assert stored is not None
        assert stored.status is SprintStatus.COMPLETED
        assert stored.end_date is not None
        scheduler.deactivate_sprint.assert_awaited()

    async def test_partial_delivery_does_not_finalize(self) -> None:
        repo = _FakeSprintRepo()

        def _to_in_review(sprint: Sprint, _t: str, _p: float) -> Sprint:
            return sprint.with_transition(SprintStatus.IN_REVIEW)

        scheduler = _scheduler(on_complete=_to_in_review)
        service = _service(sprints=repo, scheduler=scheduler)
        sprint = await service.create_sprint("proj-1")
        await service.add_task(sprint.id, str(as_uuid("t1")), 1.0)
        await service.add_task(sprint.id, str(as_uuid("t2")), 2.0)
        await service.start_sprint(sprint.id)

        task = _task("t1", status=TaskStatus.COMPLETED)
        await service.on_task_state_changed(_event(task, TaskStatus.COMPLETED))
        await service.drain()

        stored = await repo.get(sprint.id)
        assert stored is not None
        # One of two tasks done: scheduler moved it to IN_REVIEW but the
        # backlog is not fully delivered, so it must not finalize.
        assert stored.status is SprintStatus.IN_REVIEW
        scheduler.deactivate_sprint.assert_not_awaited()

    async def test_explicit_add_points_credited_without_stall(self) -> None:
        # A REST add with story points that differ from task complexity must
        # still complete: completion credits the committed per-task points.
        repo = _FakeSprintRepo()
        scheduler = _scheduler()
        service = _service(sprints=repo, scheduler=scheduler)
        sprint = await service.create_sprint("proj-1")
        await service.add_task(sprint.id, str(as_uuid("t1")), 7.0)
        await service.start_sprint(sprint.id)

        task = _task("t1", status=TaskStatus.COMPLETED, complexity=Complexity.EPIC)
        await service.on_task_state_changed(_event(task, TaskStatus.COMPLETED))
        await service.drain()

        stored = await repo.get(sprint.id)
        assert stored is not None
        assert str(as_uuid("t1")) in stored.completed_task_ids
        assert stored.story_points_completed == pytest.approx(7.0)


class TestGateHelpers:
    async def test_is_task_workable_true_without_open_sprint(self) -> None:
        service = _service()
        assert await service.is_task_workable("task-x", "proj-1") is True

    async def test_is_task_workable_gates_non_backlog_task(self) -> None:
        repo = _FakeSprintRepo()
        service = _service(sprints=repo)
        sprint = await service.create_sprint("proj-1")
        await service.add_task(sprint.id, "in-backlog", 1.0)
        await service.start_sprint(sprint.id)

        assert await service.is_task_workable("in-backlog", "proj-1") is True
        assert await service.is_task_workable("stranger", "proj-1") is False

    async def test_is_task_workable_true_when_disabled(self) -> None:
        service = _service(resolver=_resolver(enabled=False))
        assert await service.is_task_workable("anything", "proj-1") is True


class _FailHopRepo(_FakeSprintRepo):
    """Fake repo whose CAS fails a specific ``(from, to)`` hop, once configured."""

    def __init__(self, fail_from: SprintStatus, fail_to: SprintStatus) -> None:
        super().__init__()
        self._fail = (fail_from, fail_to)

    @override
    async def transition_if(
        self,
        entity_id: str,
        from_state: SprintStatus,
        to_state: SprintStatus,
        **updates: object,
    ) -> bool:
        if (from_state, to_state) == self._fail:
            return False
        return await super().transition_if(entity_id, from_state, to_state, **updates)


def _completed_sprint(number: int) -> Sprint:
    return Sprint(
        id=f"s{number}",
        project=NotBlankStr("proj-1"),
        name=NotBlankStr(f"Sprint {number}"),
        sprint_number=number,
        status=SprintStatus.COMPLETED,
        start_date="2026-01-01T00:00:00+00:00",
        end_date="2026-01-14T00:00:00+00:00",
    )


class TestConcurrencyGuards:
    async def test_velocity_history_reconstructed_oldest_first(self) -> None:
        repo = _FakeSprintRepo()
        await repo.save(_completed_sprint(1))
        await repo.save(_completed_sprint(2))
        service = _service(sprints=repo)
        history = await service._velocity_history(NotBlankStr("proj-1"))
        assert len(history) == 2

    async def test_finalize_retro_cas_lost_does_not_deactivate(self) -> None:
        repo = _FailHopRepo(SprintStatus.RETROSPECTIVE, SprintStatus.COMPLETED)

        def _to_in_review(sprint: Sprint, _t: str, _p: float) -> Sprint:
            return sprint.with_transition(SprintStatus.IN_REVIEW)

        scheduler = _scheduler(on_complete=_to_in_review)
        service = _service(sprints=repo, scheduler=scheduler)
        sprint = await service.create_sprint("proj-1")
        await service.add_task(sprint.id, str(as_uuid("t1")), 1.0)
        await service.start_sprint(sprint.id)

        task = _task("t1", status=TaskStatus.COMPLETED)
        await service.on_task_state_changed(_event(task, TaskStatus.COMPLETED))
        await service.drain()

        stored = await repo.get(sprint.id)
        assert stored is not None
        # The RETRO -> COMPLETED CAS was lost: the sprint stays at
        # RETROSPECTIVE and the scheduler is not deactivated.
        assert stored.status is SprintStatus.RETROSPECTIVE
        scheduler.deactivate_sprint.assert_not_awaited()

    async def test_finalize_review_cas_lost_stops_early(self) -> None:
        repo = _FailHopRepo(SprintStatus.IN_REVIEW, SprintStatus.RETROSPECTIVE)

        def _to_in_review(sprint: Sprint, _t: str, _p: float) -> Sprint:
            return sprint.with_transition(SprintStatus.IN_REVIEW)

        scheduler = _scheduler(on_complete=_to_in_review)
        service = _service(sprints=repo, scheduler=scheduler)
        sprint = await service.create_sprint("proj-1")
        await service.add_task(sprint.id, str(as_uuid("t1")), 1.0)
        await service.start_sprint(sprint.id)

        task = _task("t1", status=TaskStatus.COMPLETED)
        await service.on_task_state_changed(_event(task, TaskStatus.COMPLETED))
        await service.drain()

        stored = await repo.get(sprint.id)
        assert stored is not None
        assert stored.status is SprintStatus.IN_REVIEW
        scheduler.deactivate_sprint.assert_not_awaited()
