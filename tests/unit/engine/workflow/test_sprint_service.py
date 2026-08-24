"""Unit tests for the SprintService orchestration."""

from typing import Any, override
from unittest.mock import AsyncMock

import pytest

from synthorg.core.persistence_errors import QueryError
from synthorg.core.task import Task
from synthorg.core.task_enums import Complexity, Priority, TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import (
    SprintAlreadyOpenError,
    SprintBacklogFullError,
    SprintBacklogInvalidError,
    SprintNotFoundError,
    SprintTransitionConflictError,
)
from synthorg.engine.task_engine_models import TaskStateChanged
from synthorg.engine.workflow.enums import WorkflowType
from synthorg.engine.workflow.sprint_config import SprintConfig
from synthorg.engine.workflow.sprint_lifecycle import (
    STORY_POINTS_CEILING,
    Sprint,
    SprintStatus,
)
from synthorg.engine.workflow.sprint_service import SprintService
from synthorg.persistence.sprint_protocol import SprintFilterSpec
from synthorg.persistence.task_protocol import TaskRepository
from synthorg.settings.resolver_protocol import ConfigResolverProtocol
from tests._shared import FakeClock, FakeSprintRepository, as_uuid, mock_of

pytestmark = pytest.mark.unit

#: Configured mock, typed loosely for the unittest.mock API.
_Configured = Any  # type: ignore[explicit-any]


class _RecordsEveryInsert(FakeSprintRepository):
    """Remembers the status each ``save`` carried, in order."""

    def __init__(self) -> None:
        super().__init__()
        self.saved_statuses: list[SprintStatus] = []

    @override
    async def save(self, entity: Sprint) -> None:
        self.saved_statuses.append(entity.status)
        await super().save(entity)


class _RivalClaimsTheScope(FakeSprintRepository):
    """A second replica claims the scope between this one's check and its insert.

    The service reads "nothing open here", and by the time it writes, another
    writer has taken the scope. That window is precisely what a per-process
    lock cannot cover and the partial unique index can, so it is reproduced
    at the read rather than by patching a method onto the fake.

    Args:
        project: The scope the rival claims. ``None`` is the org-wide one,
            which is its own scope under the same rule.
    """

    def __init__(self, *, project: str | None = "proj-1") -> None:
        super().__init__()
        self._project = project

    @override
    async def query(
        self, filter_spec: SprintFilterSpec, *, limit: int = 50, offset: int = 0
    ) -> tuple[Sprint, ...]:
        rows = await super().query(filter_spec, limit=limit, offset=offset)
        if not rows:
            await super().save(
                Sprint(
                    id=NotBlankStr("rival"),
                    project=(
                        NotBlankStr(self._project)
                        if self._project is not None
                        else None
                    ),
                    name=NotBlankStr("Rival"),
                    sprint_number=7,
                    status=SprintStatus.ACTIVE,
                    start_date="2026-01-01T00:00:00+00:00",
                )
            )
        return rows


class _RivalTakesTheNumber(FakeSprintRepository):
    """A rival opens and COMPLETES the number this process just derived.

    The other half of the create race, and the half that reads identically
    at the boundary: the same ``ConstraintViolationError`` comes back, but
    from ``UNIQUE (project, sprint_number)`` rather than the scope index,
    and it leaves the scope free. Refusing here would deny a sprint that
    should have been opened.

    Injected at the first ``save`` rather than at a read, because the
    window that matters opens after this process has derived its number:
    the rival takes exactly that number, so the retry derives the next one
    and lands.
    """

    def __init__(self) -> None:
        super().__init__()
        self._seeded = False

    @override
    async def save(self, entity: Sprint) -> None:
        if not self._seeded:
            self._seeded = True
            await super().save(
                Sprint(
                    id=NotBlankStr("rival-done"),
                    project=NotBlankStr("proj-1"),
                    name=NotBlankStr("Rival"),
                    sprint_number=entity.sprint_number,
                    status=SprintStatus.COMPLETED,
                    start_date="2026-01-01T00:00:00+00:00",
                    end_date="2026-01-15T00:00:00+00:00",
                )
            )
        await super().save(entity)


class _FlakyAppend(FakeSprintRepository):
    """Fails the completion append a set number of times, then succeeds.

    Args:
        failures: How many attempts raise before one lands.
    """

    def __init__(self, *, failures: int) -> None:
        super().__init__()
        self._remaining = failures
        self.attempts = 0

    @override
    async def complete_task_if(self, sprint_id: str, task_id: str) -> Sprint | None:
        self.attempts += 1
        if self._remaining > 0:
            self._remaining -= 1
            msg = "store unreachable"
            raise QueryError(msg)
        return await super().complete_task_if(sprint_id, task_id)


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
        sprint_repository=sprints or FakeSprintRepository(),
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
        repo = FakeSprintRepository()
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
        """The refusal names the occupier the caller has to go and finish.

        Asserted on the attributes rather than the sentence: the occupier
        travels as fields precisely so the two places that build this
        refusal cannot drift into two different sentences.
        """
        service = _service()
        opened = await service.create_sprint("proj-1")

        with pytest.raises(SprintAlreadyOpenError) as excinfo:
            await service.create_sprint("proj-1")

        assert excinfo.value.sprint_id == opened.id
        assert excinfo.value.sprint_name == opened.name
        assert excinfo.value.sprint_status == SprintStatus.PLANNING.value

    async def test_create_sprint_refuses_when_another_writer_won(self) -> None:
        """The index refuses what this process's own check could not see.

        The check reads, then the insert writes; a second writer landing in
        between passes the first and is caught by the second, and both
        callers have to be told the same thing.
        """
        service = _service(sprints=_RivalClaimsTheScope())
        with pytest.raises(SprintAlreadyOpenError):
            await service.create_sprint("proj-1")

    async def test_create_sprint_retries_a_taken_number_on_a_free_scope(
        self,
    ) -> None:
        """The other constraint refuses the same insert and means the opposite.

        A rival that took this number and then completed its sprint leaves
        the scope free, so refusing here would deny a sprint that should
        have been opened. The number is rebuilt and the create lands.
        """
        service = _service(sprints=_RivalTakesTheNumber())

        sprint = await service.create_sprint("proj-1")

        assert sprint.status is SprintStatus.PLANNING
        assert sprint.sprint_number == 2

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

    async def test_add_task_rejects_a_duplicate(self) -> None:
        service = _service()
        sprint = await service.create_sprint("proj-1")
        await service.add_task(sprint.id, "task-a", 1.0)
        with pytest.raises(SprintBacklogInvalidError, match="already in sprint"):
            await service.add_task(sprint.id, "task-a", 1.0)

    async def test_add_task_rejects_negative_points(self) -> None:
        service = _service()
        sprint = await service.create_sprint("proj-1")
        with pytest.raises(SprintBacklogInvalidError, match=">= 0"):
            await service.add_task(sprint.id, "task-a", -1.0)

    async def test_add_task_refuses_to_cross_the_points_ceiling(self) -> None:
        """The bound is on the TOTAL, which no single caller can see.

        The API bounds each task's points by the ceiling separately, so
        two individually-admissible calls carry the sprint past it. The
        statement derives the total rather than being handed one, and
        neither table carries a CHECK, so the row that results is one the
        model refuses: without this the second call writes it.
        """
        repo = FakeSprintRepository()
        service = _service(sprints=repo)
        sprint = await service.create_sprint("proj-1")
        await service.add_task(sprint.id, "task-a", STORY_POINTS_CEILING * 0.6)

        with pytest.raises(SprintBacklogInvalidError, match="ceiling"):
            await service.add_task(sprint.id, "task-b", STORY_POINTS_CEILING * 0.6)

        stored = await repo.get(sprint.id)
        assert stored is not None
        assert stored.task_ids == ("task-a",)
        assert stored.story_points_committed <= STORY_POINTS_CEILING

    async def test_add_task_allows_exactly_the_ceiling(self) -> None:
        service = _service()
        sprint = await service.create_sprint("proj-1")
        updated = await service.add_task(sprint.id, "task-a", STORY_POINTS_CEILING)
        assert updated.story_points_committed == pytest.approx(STORY_POINTS_CEILING)

    async def test_successive_adds_accumulate(self) -> None:
        """Two adds of different tasks compose into one backlog.

        Sequential, and named for it: the service serialises its writes
        behind one lock, so dispatching these together would still award
        them in series and prove nothing about contention. The race this
        guards lives one layer down and is exercised against the real
        statements in the persistence conformance suite.
        """
        repo = FakeSprintRepository()
        service = _service(sprints=repo)
        sprint = await service.create_sprint("proj-1")

        await service.add_task(sprint.id, "task-a", 2.0)
        second = await service.add_task(sprint.id, "task-b", 3.0)

        assert set(second.task_ids) == {"task-a", "task-b"}
        assert second.story_points_committed == pytest.approx(5.0)

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
        repo = FakeSprintRepository()
        service = _service(sprints=repo, resolver=_resolver(enabled=False))
        await service.on_task_state_changed(
            _event(_task("t1", status=TaskStatus.ASSIGNED), TaskStatus.ASSIGNED)
        )
        assert await repo.count(SprintFilterSpec()) == 0

    async def test_non_agile_workflow_is_noop(self) -> None:
        repo = FakeSprintRepository()
        service = _service(sprints=repo, resolver=_resolver(agile=False))
        await service.on_task_state_changed(
            _event(_task("t1", status=TaskStatus.ASSIGNED), TaskStatus.ASSIGNED)
        )
        assert await repo.count(SprintFilterSpec()) == 0

    async def test_assigned_auto_creates_and_starts_sprint(self) -> None:
        repo = FakeSprintRepository()
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

    async def test_auto_created_sprint_never_exists_as_planning(self) -> None:
        """One insert, already ACTIVE: no window for a stranded shell.

        A separate activation hop after the insert leaves a PLANNING sprint
        whenever the process dies or the CAS is lost in between. That row is
        never offered to an operator, nothing re-drives it, and it holds the
        scope's one open slot against every later task for good.
        """
        repo = _RecordsEveryInsert()
        trigger = _task("t1", status=TaskStatus.ASSIGNED)
        tasks = mock_of[TaskRepository](query=AsyncMock(return_value=(trigger,)))
        service = _service(sprints=repo, tasks=tasks)

        await service.on_task_state_changed(_event(trigger, TaskStatus.ASSIGNED))
        await service.drain()

        assert repo.saved_statuses == [SprintStatus.ACTIVE]

    async def test_second_assigned_does_not_create_duplicate(self) -> None:
        repo = FakeSprintRepository()
        trigger = _task("t1", status=TaskStatus.ASSIGNED)
        tasks = mock_of[TaskRepository](query=AsyncMock(return_value=(trigger,)))
        service = _service(sprints=repo, tasks=tasks)
        await service.on_task_state_changed(_event(trigger, TaskStatus.ASSIGNED))
        await service.on_task_state_changed(_event(trigger, TaskStatus.ASSIGNED))
        assert await repo.count(SprintFilterSpec(project="proj-1")) == 1

    async def test_auto_create_excludes_terminal_tasks(self) -> None:
        repo = FakeSprintRepository()
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
        repo = FakeSprintRepository()
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
        repo = FakeSprintRepository()

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
        repo = FakeSprintRepository()

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
        repo = FakeSprintRepository()
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
        repo = FakeSprintRepository()
        service = _service(sprints=repo)
        sprint = await service.create_sprint("proj-1")
        await service.add_task(sprint.id, "in-backlog", 1.0)
        await service.start_sprint(sprint.id)

        assert await service.is_task_workable("in-backlog", "proj-1") is True
        assert await service.is_task_workable("stranger", "proj-1") is False

    async def test_is_task_workable_true_when_disabled(self) -> None:
        service = _service(resolver=_resolver(enabled=False))
        assert await service.is_task_workable("anything", "proj-1") is True


class _FailHopRepo(FakeSprintRepository):
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
        repo = FakeSprintRepository()
        service = _service(sprints=repo)
        sprint = await service.create_sprint("proj-1")
        await service.add_task(sprint.id, str(as_uuid("t1")), 1.0)
        await service.start_sprint(sprint.id)
        # Record the delivery out of band, so the service's own append finds
        # the id already there and returns None.
        await repo.complete_task_if(sprint.id, str(as_uuid("t1")))

        task = _task("t1", status=TaskStatus.COMPLETED)
        await service.on_task_state_changed(_event(task, TaskStatus.COMPLETED))
        await service.drain()

        stored = await repo.get(sprint.id)
        assert stored is not None
        assert stored.status is SprintStatus.COMPLETED

    async def test_completion_credits_points_once(self) -> None:
        """A duplicate completion event must not double the credit."""
        repo = FakeSprintRepository()
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

    async def test_a_transient_append_failure_is_retried(self) -> None:
        """Nothing re-fires a completion, so a store blip must not drop it.

        The recovery sweep cannot repair this one: it advances lifecycle
        state and never re-derives ``completed_task_ids``, so a completion
        lost here is lost for good and the sprint can never read delivered.
        """
        repo = _FlakyAppend(failures=2)
        service = _service(sprints=repo)
        sprint = await service.create_sprint("proj-1")
        await service.add_task(sprint.id, str(as_uuid("t1")), 1.0)
        await service.start_sprint(sprint.id)

        task = _task("t1", status=TaskStatus.COMPLETED)
        await service.on_task_state_changed(_event(task, TaskStatus.COMPLETED))
        await service.drain()

        assert repo.attempts == 3
        stored = await repo.get(sprint.id)
        assert stored is not None
        assert str(as_uuid("t1")) in stored.completed_task_ids

    async def test_a_persistent_append_failure_surfaces(self) -> None:
        """Past the retry ladder it is re-raised, not swallowed here.

        The observer's catch-all is what finally absorbs it, but only after
        this path has logged the divergence distinctly: the task's own
        status is authoritative and unaffected, while the sprint's backlog
        now disagrees with it and needs an operator.
        """
        repo = _FlakyAppend(failures=99)
        service = _service(sprints=repo)
        sprint = await service.create_sprint("proj-1")
        await service.add_task(sprint.id, str(as_uuid("t1")), 1.0)
        await service.start_sprint(sprint.id)

        task = _task("t1", status=TaskStatus.COMPLETED)
        # The observer is best-effort by contract, so this must not raise
        # into the engine's dispatch loop.
        await service.on_task_state_changed(_event(task, TaskStatus.COMPLETED))
        await service.drain()

        stored = await repo.get(sprint.id)
        assert stored is not None
        assert stored.completed_task_ids == ()
        assert stored.status is SprintStatus.ACTIVE

    async def test_org_wide_create_race_is_refused_too(self) -> None:
        """The org-wide scope is guarded by the same index as a project's.

        ``project=None`` on a filter means "no project predicate", so the
        scope has to be asked for by name; a guard that reused the unset
        value would answer about somebody else's sprint.
        """
        service = _service(sprints=_RivalClaimsTheScope(project=None))
        with pytest.raises(SprintAlreadyOpenError):
            await service.create_sprint(None)
