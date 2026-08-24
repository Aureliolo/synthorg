"""Unit tests for the SprintService orchestration."""

from typing import Any, override
from unittest.mock import AsyncMock

import pytest

from synthorg.core.persistence_errors import ConstraintViolationError
from synthorg.core.task import Task
from synthorg.core.task_enums import Complexity, Priority, TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import (
    SprintAlreadyOpenError,
    SprintBacklogFullError,
    SprintNotFoundError,
    SprintTransitionConflictError,
)
from synthorg.engine.task_engine_models import TaskStateChanged
from synthorg.engine.workflow.enums import WorkflowType
from synthorg.engine.workflow.sprint_config import SprintConfig
from synthorg.engine.workflow.sprint_lifecycle import (
    OPEN_SPRINT_STATUSES,
    Sprint,
    SprintStatus,
)
from synthorg.engine.workflow.sprint_service import SprintService
from synthorg.persistence.sprint_protocol import SprintFilterSpec
from synthorg.persistence.task_protocol import TaskRepository
from synthorg.settings.resolver_protocol import ConfigResolverProtocol
from tests._shared import FakeClock, as_uuid, mock_of

pytestmark = pytest.mark.unit

#: Configured mock, typed loosely for the unittest.mock API.
_Configured = Any  # type: ignore[explicit-any]


class _FakeSprintRepo:
    """In-memory ``SprintRepository`` for driving the service end-to-end.

    Models the database's constraints, not just its methods. The
    one-open-sprint-per-scope index and the completion guard are the two
    things the service now relies on being true, so a fake that accepted
    everything would keep this suite green over exactly the invariants it
    is meant to be exercising.
    """

    def __init__(self) -> None:
        self._rows: dict[str, Sprint] = {}

    @staticmethod
    def _scope_key(sprint: Sprint) -> str:
        """Mirror the index's ``COALESCE(project, '')`` key.

        Returns:
            The scope the sprint occupies while it is not completed.
        """
        return sprint.project or ""

    async def save(self, entity: Sprint) -> None:
        """Upsert, refusing a second open sprint in one scope.

        Raises:
            ConstraintViolationError: When the scope already holds a
                different non-completed sprint, as the partial unique
                index does.
        """
        if entity.status is not SprintStatus.COMPLETED:
            key = self._scope_key(entity)
            for existing in self._rows.values():
                if (
                    existing.id != entity.id
                    and existing.status is not SprintStatus.COMPLETED
                    and self._scope_key(existing) == key
                ):
                    msg = f"scope {key!r} already has open sprint {existing.id!r}"
                    raise ConstraintViolationError(msg, constraint=msg)
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
            and (not filter_spec.org_wide_only or s.project is None)
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

    async def complete_task_if(
        self,
        sprint_id: str,
        task_id: str,
        story_points: float,
    ) -> Sprint | None:
        """Append *task_id* iff the same guard the SQL applies holds.

        Returns:
            The post-image, or ``None`` when the guard did not match.
        """
        row = self._rows.get(sprint_id)
        if row is None or row.status not in OPEN_SPRINT_STATUSES:
            return None
        if task_id not in row.task_ids or task_id in row.completed_task_ids:
            return None
        updated = row.model_copy(
            update={
                "completed_task_ids": (*row.completed_task_ids, task_id),
                "story_points_completed": row.story_points_completed + story_points,
            }
        )
        self._rows[sprint_id] = updated
        return updated


class _RivalClaimsTheScope(_FakeSprintRepo):
    """A second replica claims the scope between this one's check and its insert.

    The service reads "nothing open here", and by the time it writes, another
    writer has taken the scope. That window is precisely what a per-process
    lock cannot cover and the partial unique index can, so it is reproduced
    at the read rather than by patching a method onto the fake.
    """

    @override
    async def query(
        self, filter_spec: SprintFilterSpec, *, limit: int = 50, offset: int = 0
    ) -> tuple[Sprint, ...]:
        rows = await super().query(filter_spec, limit=limit, offset=offset)
        if not rows:
            await super().save(
                Sprint(
                    id=NotBlankStr("rival"),
                    project=NotBlankStr("proj-1"),
                    name=NotBlankStr("Rival"),
                    sprint_number=7,
                    status=SprintStatus.ACTIVE,
                    start_date="2026-01-01T00:00:00+00:00",
                )
            )
        return rows


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


def _service(
    *,
    sprints: _Configured = None,
    tasks: _Configured = None,
    resolver: _Configured = None,
    sprint_config: SprintConfig | None = None,
) -> SprintService:
    task_repo = tasks or mock_of[TaskRepository](query=AsyncMock(return_value=()))
    return SprintService(
        sprint_repository=sprints or _FakeSprintRepo(),
        task_repository=task_repo,
        config_resolver=resolver or _resolver(),
        sprint_config=sprint_config,
        clock=FakeClock(),
    )


def _completed(sprint: Sprint) -> Sprint:
    """Return *sprint* in its terminal state, freeing its scope.

    Returns:
        A COMPLETED copy carrying the dates that status requires.
    """
    return sprint.model_copy(
        update={
            "status": SprintStatus.COMPLETED,
            "start_date": "2026-01-01T00:00:00+00:00",
            "end_date": "2026-01-15T00:00:00+00:00",
        }
    )


class TestExplicitControl:
    async def test_create_sprint_numbers_sequentially(self) -> None:
        repo = _FakeSprintRepo()
        service = _service(sprints=repo)
        first = await service.create_sprint("proj-1")
        # A scope runs one sprint at a time, so the next one follows the
        # first finishing rather than sitting beside it.
        await repo.save(_completed(first))
        second = await service.create_sprint("proj-1")
        assert first.sprint_number == 1
        assert second.sprint_number == 2
        assert first.status is SprintStatus.PLANNING

    async def test_create_sprint_refuses_while_one_is_open(self) -> None:
        service = _service()
        opened = await service.create_sprint("proj-1")
        with pytest.raises(SprintAlreadyOpenError, match=opened.id):
            await service.create_sprint("proj-1")

    async def test_create_sprint_refuses_when_another_writer_won(self) -> None:
        """The index refuses what this process's own check could not see.

        The check reads, then the insert writes; a second writer landing in
        between passes the first and is caught by the second, and both
        callers have to be told the same thing.
        """
        service = _service(sprints=_RivalClaimsTheScope())
        with pytest.raises(SprintAlreadyOpenError):
            await service.create_sprint("proj-1")

    async def test_create_sprint_scopes_org_wide_separately(self) -> None:
        """An org-wide sprint is its own scope, not every project's.

        ``project=None`` on a filter means "no project predicate", so a
        guard that reused it would refuse an org-wide sprint whenever any
        project had one open.
        """
        service = _service()
        await service.create_sprint("proj-1")
        org_wide = await service.create_sprint(None)
        assert org_wide.project is None

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

    async def test_start_sprint_activates(self) -> None:
        service = _service()
        sprint = await service.create_sprint("proj-1")
        started = await service.start_sprint(sprint.id)
        await service.drain()
        assert started.status is SprintStatus.ACTIVE
        assert started.start_date is not None

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
        service = _service(sprints=repo, tasks=tasks)

        await service.on_task_state_changed(_event(trigger, TaskStatus.ASSIGNED))
        await service.drain()

        sprints = await repo.query(SprintFilterSpec(project="proj-1"))
        assert len(sprints) == 1
        active = sprints[0]
        assert active.status is SprintStatus.ACTIVE
        assert set(active.task_ids) == {str(trigger.id), str(as_uuid("t2"))}

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

        service = _service(sprints=repo)
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

    async def test_partial_delivery_does_not_finalize(self) -> None:
        repo = _FakeSprintRepo()

        service = _service(sprints=repo)
        sprint = await service.create_sprint("proj-1")
        await service.add_task(sprint.id, str(as_uuid("t1")), 1.0)
        await service.add_task(sprint.id, str(as_uuid("t2")), 2.0)
        await service.start_sprint(sprint.id)

        task = _task("t1", status=TaskStatus.COMPLETED)
        await service.on_task_state_changed(_event(task, TaskStatus.COMPLETED))
        await service.drain()

        stored = await repo.get(sprint.id)
        assert stored is not None
        # One of two tasks done: the backlog is not fully delivered, so
        # review must not open and the sprint must not finalize.
        assert stored.status is SprintStatus.ACTIVE

    async def test_explicit_add_points_credited_without_stall(self) -> None:
        # A REST add with story points that differ from task complexity must
        # still complete: completion credits the committed per-task points.
        repo = _FakeSprintRepo()
        service = _service(sprints=repo)
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


class TestConcurrencyGuards:
    async def test_finalize_retro_cas_lost_leaves_sprint_in_retrospective(self) -> None:
        repo = _FailHopRepo(SprintStatus.RETROSPECTIVE, SprintStatus.COMPLETED)

        service = _service(sprints=repo)
        sprint = await service.create_sprint("proj-1")
        await service.add_task(sprint.id, str(as_uuid("t1")), 1.0)
        await service.start_sprint(sprint.id)

        task = _task("t1", status=TaskStatus.COMPLETED)
        await service.on_task_state_changed(_event(task, TaskStatus.COMPLETED))
        await service.drain()

        stored = await repo.get(sprint.id)
        assert stored is not None
        # The RETRO -> COMPLETED CAS was lost, so the sprint stays at
        # RETROSPECTIVE rather than reporting a finish it never made.
        assert stored.status is SprintStatus.RETROSPECTIVE

    async def test_finalize_review_cas_lost_stops_early(self) -> None:
        repo = _FailHopRepo(SprintStatus.IN_REVIEW, SprintStatus.RETROSPECTIVE)

        service = _service(sprints=repo)
        sprint = await service.create_sprint("proj-1")
        await service.add_task(sprint.id, str(as_uuid("t1")), 1.0)
        await service.start_sprint(sprint.id)

        task = _task("t1", status=TaskStatus.COMPLETED)
        await service.on_task_state_changed(_event(task, TaskStatus.COMPLETED))
        await service.drain()

        stored = await repo.get(sprint.id)
        assert stored is not None
        assert stored.status is SprintStatus.IN_REVIEW

    async def test_auto_create_race_loss_is_not_a_failure(self) -> None:
        """Losing the create race is the correct outcome, not an error.

        Two replicas handling ASSIGNED events for one project both read
        "nothing open here" before either insert lands. The index refuses
        the second, and that replica must go quiet rather than surface a
        failure for a scope that now has exactly the sprint it needed.
        """
        repo = _RivalClaimsTheScope()
        trigger = _task("t1", status=TaskStatus.ASSIGNED)
        tasks = mock_of[TaskRepository](query=AsyncMock(return_value=(trigger,)))
        service = _service(sprints=repo, tasks=tasks)

        await service.on_task_state_changed(_event(trigger, TaskStatus.ASSIGNED))
        await service.drain()

        assert [s.id for s in await repo.list_items()] == ["rival"]

    async def test_completion_drives_the_tail_after_a_lost_append(self) -> None:
        """A guard that did not match still has to reach the tail.

        Another writer recorded this completion first. If this process took
        that as "nothing to do", and the other process did the same for its
        own completion, a fully-delivered sprint would sit ACTIVE with no
        completion left to fire.
        """
        repo = _FakeSprintRepo()
        service = _service(sprints=repo)
        sprint = await service.create_sprint("proj-1")
        await service.add_task(sprint.id, str(as_uuid("t1")), 1.0)
        await service.start_sprint(sprint.id)
        # Record the delivery out of band, so the service's own append finds
        # the id already there and returns None.
        await repo.complete_task_if(sprint.id, str(as_uuid("t1")), 1.0)

        task = _task("t1", status=TaskStatus.COMPLETED)
        await service.on_task_state_changed(_event(task, TaskStatus.COMPLETED))
        await service.drain()

        stored = await repo.get(sprint.id)
        assert stored is not None
        assert stored.status is SprintStatus.COMPLETED

    async def test_completion_credits_points_once(self) -> None:
        """A duplicate completion event must not double the credit."""
        repo = _FakeSprintRepo()
        service = _service(sprints=repo)
        sprint = await service.create_sprint("proj-1")
        await service.add_task(sprint.id, str(as_uuid("t1")), 3.0)
        await service.add_task(sprint.id, str(as_uuid("t2")), 5.0)
        await service.start_sprint(sprint.id)

        task = _task("t1", status=TaskStatus.COMPLETED)
        await service.on_task_state_changed(_event(task, TaskStatus.COMPLETED))
        await service.on_task_state_changed(_event(task, TaskStatus.COMPLETED))
        await service.drain()

        stored = await repo.get(sprint.id)
        assert stored is not None
        assert stored.completed_task_ids == (str(as_uuid("t1")),)
        assert stored.story_points_completed == pytest.approx(3.0)
