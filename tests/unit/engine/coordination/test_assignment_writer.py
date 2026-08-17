"""A wave's assignments are persisted before the wave dispatches."""

import asyncio
from datetime import date
from typing import Any
from unittest.mock import AsyncMock

import pytest
import structlog

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.persistence_errors import QueryError
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus, TaskType
from synthorg.core.task_transitions import VALID_TRANSITIONS
from synthorg.core.types import NotBlankStr
from synthorg.engine.coordination.assignment_writer import AssignmentWriter
from synthorg.engine.errors import CoordinationError
from synthorg.engine.parallel_models import AgentAssignment, ParallelExecutionGroup
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import TaskMutationResult
from tests._shared import as_uuid, mock_of

pytestmark = pytest.mark.unit


def _identity(label: str) -> AgentIdentity:
    return AgentIdentity(
        id=as_uuid(label),
        name="Agent",
        role="Developer",
        department="Engineering",
        model=ModelConfig(provider="test-provider", model_id="test-basic-001"),
        hiring_date=date(2026, 1, 1),
    )


def _task(
    label: str,
    *,
    status: TaskStatus = TaskStatus.CREATED,
    assigned_to: str | None = None,
) -> Task:
    return Task(
        id=as_uuid(label),
        title=f"Task {label}",
        description="A detailed test task description",
        type=TaskType.DEVELOPMENT,
        project="test-project",
        created_by="test-creator",
        status=status,
        assigned_to=assigned_to,
    )


def _group(
    *assignments: AgentAssignment,
    group_id: str = "wave-0",
    dag_level: int = 0,
) -> ParallelExecutionGroup:
    return ParallelExecutionGroup(
        group_id=NotBlankStr(group_id),
        assignments=assignments,
        dag_level=dag_level,
    )


def _engine(  # type: ignore[explicit-any]  # mock_of returns Any
    *,
    live: Task | None,
    result: TaskMutationResult | None = None,
) -> Any:
    return mock_of[TaskEngine](
        get_task=AsyncMock(return_value=live),
        submit=AsyncMock(
            return_value=result
            or TaskMutationResult(request_id="r", success=True, version=2)
        ),
    )


class TestAssignmentWriter:
    async def test_created_subtask_is_assigned_before_dispatch(self) -> None:
        """The engine, not the dispatcher's copy, decides the task is ASSIGNED."""
        identity = _identity("agent-a")
        created = _task("task-a")
        assigned = _task(
            "task-a", status=TaskStatus.ASSIGNED, assigned_to=str(identity.id)
        )
        engine = _engine(
            live=created,
            result=TaskMutationResult(
                request_id="r", success=True, task=assigned, version=2
            ),
        )
        writer = AssignmentWriter(engine)

        persisted = await writer.persist(
            _group(AgentAssignment(identity=identity, task=created))
        )

        mutation = engine.submit.call_args.args[0]
        assert mutation.target_status == TaskStatus.ASSIGNED
        assert mutation.overrides["assigned_to"] == str(identity.id)
        assert persisted.assignments[0].task.status == TaskStatus.ASSIGNED
        assert persisted.assignments[0].task.assigned_to == str(identity.id)

    async def test_already_assigned_to_this_agent_is_not_rewritten(self) -> None:
        """A re-dispatched subtask keeps the row the engine already holds."""
        identity = _identity("agent-a")
        live = _task(
            "task-a", status=TaskStatus.IN_PROGRESS, assigned_to=str(identity.id)
        )
        engine = _engine(live=live)
        writer = AssignmentWriter(engine)

        persisted = await writer.persist(
            _group(AgentAssignment(identity=identity, task=live))
        )

        engine.submit.assert_not_awaited()
        assert persisted.assignments[0].task.status == TaskStatus.IN_PROGRESS

    async def test_rejected_assignment_fails_the_wave(self) -> None:
        """A refused assignment must not dispatch behind the engine's back."""
        identity = _identity("agent-a")
        created = _task("task-a")
        engine = _engine(
            live=created,
            result=TaskMutationResult(
                request_id="r",
                success=False,
                error="invalid transition",
                error_code="validation",
            ),
        )
        writer = AssignmentWriter(engine)

        with pytest.raises(CoordinationError, match="invalid transition"):
            await writer.persist(
                _group(AgentAssignment(identity=identity, task=created))
            )

    async def test_vanished_subtask_fails_the_wave(self) -> None:
        """A subtask the engine has no row for cannot be dispatched."""
        identity = _identity("agent-a")
        engine = _engine(live=None)
        writer = AssignmentWriter(engine)

        with pytest.raises(CoordinationError, match="no longer exists"):
            await writer.persist(
                _group(AgentAssignment(identity=identity, task=_task("task-a")))
            )

    async def test_no_engine_leaves_the_group_untouched(self) -> None:
        """Without a central engine the dispatcher's own copy is authoritative."""
        identity = _identity("agent-a")
        group = _group(AgentAssignment(identity=identity, task=_task("task-a")))
        writer = AssignmentWriter(None)

        assert await writer.persist(group) is group


class TestPartialWaveIsReleased:
    """A wave that fails partway must not leave siblings owned by nobody.

    Assignments are written one at a time, so a refusal on the third leaves
    the first two ASSIGNED to agents the dispatcher has already given up on:
    rows nothing runs and nothing watches.
    """

    async def test_a_failed_wave_releases_what_it_already_assigned(self) -> None:
        first, second = _identity("agent-a"), _identity("agent-b")
        one, two = _task("task-a"), _task("task-b")
        assigned_one = _task(
            "task-a", status=TaskStatus.ASSIGNED, assigned_to=str(first.id)
        )
        engine = mock_of[TaskEngine](
            get_task=AsyncMock(side_effect=[one, two]),
            submit=AsyncMock(
                side_effect=[
                    TaskMutationResult(
                        request_id="r", success=True, task=assigned_one, version=2
                    ),
                    TaskMutationResult(
                        request_id="r",
                        success=False,
                        error="invalid transition",
                        error_code="validation",
                    ),
                    TaskMutationResult(request_id="r", success=True, version=3),
                ]
            ),
        )

        with pytest.raises(CoordinationError):
            await AssignmentWriter(engine).persist(
                _group(
                    AgentAssignment(identity=first, task=one),
                    AgentAssignment(identity=second, task=two),
                )
            )

        release = engine.submit.await_args_list[-1].args[0]
        assert release.task_id == str(one.id)
        # BLOCKED, not CANCELLED: the work is still wanted and a replan wave
        # reassigns from there.
        assert release.target_status is TaskStatus.BLOCKED

    async def test_a_subtask_another_wave_owns_is_not_released(self) -> None:
        """It was returned untouched, so releasing it would block a running run."""
        identity = _identity("agent-a")
        running = _task(
            "task-a", status=TaskStatus.IN_PROGRESS, assigned_to=str(identity.id)
        )
        engine = mock_of[TaskEngine](
            get_task=AsyncMock(side_effect=[running, None]),
            submit=AsyncMock(),
        )

        with pytest.raises(CoordinationError, match="no longer exists"):
            await AssignmentWriter(engine).persist(
                _group(
                    AgentAssignment(identity=identity, task=running),
                    AgentAssignment(
                        identity=_identity("agent-b"), task=_task("task-b")
                    ),
                )
            )

        engine.submit.assert_not_awaited()

    async def test_a_failed_release_does_not_replace_the_wave_diagnosis(self) -> None:
        first = _identity("agent-a")
        one, two = _task("task-a"), _task("task-b")
        assigned_one = _task(
            "task-a", status=TaskStatus.ASSIGNED, assigned_to=str(first.id)
        )
        engine = mock_of[TaskEngine](
            get_task=AsyncMock(side_effect=[one, two]),
            submit=AsyncMock(
                side_effect=[
                    TaskMutationResult(
                        request_id="r", success=True, task=assigned_one, version=2
                    ),
                    TaskMutationResult(
                        request_id="r",
                        success=False,
                        error="invalid transition",
                        error_code="validation",
                    ),
                    QueryError("engine down"),
                ]
            ),
        )

        with pytest.raises(CoordinationError, match="invalid transition"):
            await AssignmentWriter(engine).persist(
                _group(
                    AgentAssignment(identity=first, task=one),
                    AgentAssignment(identity=_identity("agent-b"), task=two),
                )
            )

        # Three submits: the assignment, the refusal, and the release that
        # failed. Without this the test would pass even if `_release` never
        # ran, since an unused side effect is silent.
        assert engine.submit.await_count == 3

    async def test_a_cancelled_wave_still_releases(self) -> None:
        """Cancellation is not an ``Exception`` and leaks the same rows.

        A wave cancelled partway (a shutdown, a coordination timeout) has
        already moved the subtasks before the cancellation point, and nothing
        else frees them.
        """
        first = _identity("agent-a")
        one, two = _task("task-a"), _task("task-b")
        assigned_one = _task(
            "task-a", status=TaskStatus.ASSIGNED, assigned_to=str(first.id)
        )
        engine = mock_of[TaskEngine](
            get_task=AsyncMock(side_effect=[one, two]),
            submit=AsyncMock(
                side_effect=[
                    TaskMutationResult(
                        request_id="r", success=True, task=assigned_one, version=2
                    ),
                    asyncio.CancelledError(),
                    TaskMutationResult(request_id="r", success=True, version=3),
                ]
            ),
        )

        with pytest.raises(asyncio.CancelledError):
            await AssignmentWriter(engine).persist(
                _group(
                    AgentAssignment(identity=first, task=one),
                    AgentAssignment(identity=_identity("agent-b"), task=two),
                )
            )

        release = engine.submit.await_args_list[-1].args[0]
        assert release.task_id == str(one.id)
        assert release.target_status is TaskStatus.BLOCKED

    async def test_a_rejected_release_is_reported(self) -> None:
        """The engine refuses by returning, so an unchecked result is silence.

        A refused release leaves the row ASSIGNED to an agent nothing will
        run, which is the state the release exists to prevent, so it has to
        be as visible as a release that raised.
        """
        first = _identity("agent-a")
        one, two = _task("task-a"), _task("task-b")
        assigned_one = _task(
            "task-a", status=TaskStatus.ASSIGNED, assigned_to=str(first.id)
        )
        engine = mock_of[TaskEngine](
            get_task=AsyncMock(side_effect=[one, two]),
            submit=AsyncMock(
                side_effect=[
                    TaskMutationResult(
                        request_id="r", success=True, task=assigned_one, version=2
                    ),
                    TaskMutationResult(
                        request_id="r",
                        success=False,
                        error="invalid transition",
                        error_code="validation",
                    ),
                    TaskMutationResult(
                        request_id="r",
                        success=False,
                        error="assigned -> blocked refused",
                        error_code="validation",
                    ),
                ]
            ),
        )

        with (
            structlog.testing.capture_logs() as captured,
            pytest.raises(CoordinationError, match="invalid transition"),
        ):
            await AssignmentWriter(engine).persist(
                _group(
                    AgentAssignment(identity=first, task=one),
                    AgentAssignment(identity=_identity("agent-b"), task=two),
                )
            )

        warnings = [e for e in captured if e.get("log_level") == "warning"]
        assert any(
            entry.get("subtask_id") == str(one.id)
            and entry.get("error_type") == "TaskMutationRejected"
            for entry in warnings
        )

    async def test_an_engine_error_mid_wave_still_releases(self) -> None:
        """A refused assignment is not the only way a wave dies partway.

        ``get_task`` and ``submit`` raise engine and persistence errors too,
        and those leak exactly the same half-assigned rows.
        """
        first = _identity("agent-a")
        one, two = _task("task-a"), _task("task-b")
        assigned_one = _task(
            "task-a", status=TaskStatus.ASSIGNED, assigned_to=str(first.id)
        )
        engine = mock_of[TaskEngine](
            get_task=AsyncMock(side_effect=[one, two]),
            submit=AsyncMock(
                side_effect=[
                    TaskMutationResult(
                        request_id="r", success=True, task=assigned_one, version=2
                    ),
                    QueryError("engine down"),
                    TaskMutationResult(request_id="r", success=True, version=3),
                ]
            ),
        )

        with pytest.raises(QueryError):
            await AssignmentWriter(engine).persist(
                _group(
                    AgentAssignment(identity=first, task=one),
                    AgentAssignment(identity=_identity("agent-b"), task=two),
                )
            )

        release = engine.submit.await_args_list[-1].args[0]
        assert release.task_id == str(one.id)
        assert release.target_status is TaskStatus.BLOCKED


class TestRacingWaves:
    """Two waves reaching for one subtask: the loser fails, it does not steal."""

    def test_reassigning_an_assigned_subtask_is_not_a_legal_hop(self) -> None:
        """The invariant the writer's stale read leans on.

        The writer reads, then submits, so its read can go stale. What stops
        the loser rewriting ``assigned_to`` under a running agent is that the
        engine re-reads the row under its single-writer loop and finds no
        ``ASSIGNED -> ASSIGNED`` edge. If that edge is ever added, the writer
        needs its own guard and this test is where that shows up.
        """
        assert TaskStatus.ASSIGNED not in VALID_TRANSITIONS[TaskStatus.ASSIGNED]

    async def test_the_losing_wave_fails_rather_than_stealing_the_subtask(
        self,
    ) -> None:
        """The engine refuses; the writer must not dispatch anyway."""
        identity = _identity("agent-b")
        # What this wave read: still unassigned. What the engine holds by the
        # time the mutation applies: assigned to the wave that got there first.
        stale = _task("task-a")
        engine = _engine(
            live=stale,
            result=TaskMutationResult(
                request_id="r",
                success=False,
                error="Invalid transition: assigned -> assigned",
                error_code="validation",
            ),
        )
        writer = AssignmentWriter(engine)

        with pytest.raises(CoordinationError) as info:
            await writer.persist(_group(AgentAssignment(identity=identity, task=stale)))

        message = str(info.value)
        # Both halves, because the pair is the diagnosis: what this wave saw
        # and what the engine refused. Reporting only the stale read sent an
        # operator looking for a CREATED row that no longer existed.
        assert "read as 'created' before dispatch" in message
        assert "assigned -> assigned" in message


class TestAbandonNamesWhatActuallyHappened:
    """A park is read by a replan, so its reason has to be true.

    An execution group is one round of AGENTS, not one level of the DAG:
    ``_rounds_by_agent`` splits a level across as many groups as its busiest
    agent needs. So the groups after the one a run stopped at are a mix of
    work genuinely below the stop and siblings of it, and only the first
    kind lost an input.
    """

    @staticmethod
    def _reason_for(engine: Any) -> str:  # type: ignore[explicit-any]  # mock
        mutation = engine.submit.await_args.args[0]
        return str(mutation.overrides["blocked_reason"])

    async def test_a_sibling_of_the_stopped_wave_is_not_a_dependency_failure(
        self,
    ) -> None:
        """It sits at the same level, so nothing it declared has failed.

        The one-developer case makes this the common shape rather than the
        exotic one: every subtask becomes its own group, so the groups after
        the stop are the whole rest of the plan, all of it at level 0.
        """
        identity = _identity("agent-1")
        sibling = _task("sibling")
        engine = _engine(live=sibling)
        writer = AssignmentWriter(engine)

        parked = await writer.abandon_remaining(
            [
                _group(
                    AgentAssignment(identity=identity, task=_task("stopped")),
                    group_id="wave-0",
                    dag_level=0,
                ),
                _group(
                    AgentAssignment(identity=identity, task=sibling),
                    group_id="wave-0-1",
                    dag_level=0,
                ),
            ],
            stopped_at=0,
        )

        # It still parks: a row left at CREATED has no exit at all.
        assert parked == 1
        assert self._reason_for(engine) == "run_stopped"

    async def test_work_below_the_stop_is_a_dependency_failure(self) -> None:
        """A strictly later level may genuinely have lost its inputs."""
        identity = _identity("agent-1")
        downstream = _task("downstream")
        engine = _engine(live=downstream)
        writer = AssignmentWriter(engine)

        parked = await writer.abandon_remaining(
            [
                _group(
                    AgentAssignment(identity=identity, task=_task("stopped")),
                    group_id="wave-0",
                    dag_level=0,
                ),
                _group(
                    AgentAssignment(identity=identity, task=downstream),
                    group_id="wave-1",
                    dag_level=1,
                ),
            ],
            stopped_at=0,
        )

        assert parked == 1
        assert self._reason_for(engine) == "dependency_failed"

    async def test_a_wave_that_failed_parks_its_own_undispatched_rows(self) -> None:
        """``abandon_remaining`` skips the stopped wave; a raise strands it.

        ``persist`` gives up on the first refused hop and the release reverts
        only the rows it had already moved, so the rest never leave CREATED.
        Nothing else parks them, and CREATED is not a non-delivering status,
        so the next wave's gate reads them as still on their way.
        """
        identity = _identity("agent-1")
        stranded = _task("stranded")
        engine = _engine(live=stranded)
        writer = AssignmentWriter(engine)

        parked = await writer.abandon_stranded(
            _group(AgentAssignment(identity=identity, task=stranded)),
            stopped_at=2,
        )

        assert parked == 1
        assert self._reason_for(engine) == "run_stopped"

    async def test_a_row_that_already_ran_is_left_alone(self) -> None:
        """Anything that ran owns its own outcome."""
        identity = _identity("agent-1")
        ran = _task("ran", status=TaskStatus.IN_PROGRESS, assigned_to="agent-1")
        engine = _engine(live=ran)
        writer = AssignmentWriter(engine)

        parked = await writer.abandon_stranded(
            _group(AgentAssignment(identity=identity, task=ran)),
            stopped_at=0,
        )

        assert parked == 0
        engine.submit.assert_not_awaited()
