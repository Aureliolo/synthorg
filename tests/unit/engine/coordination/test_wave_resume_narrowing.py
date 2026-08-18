"""A wave dispatches what still needs doing, and nothing that is done.

The invariant: waves are rebuilt from the plan's items, which record what the
plan WANTS rather than what has happened, so anything that re-enters a wave
loop over a partly-run plan must not re-propose the levels already delivered.
Without that, a resumed run pays for finished work and then fails on it, since
the engine refuses ``COMPLETED -> ASSIGNED``.
"""

from datetime import date
from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.clock import Clock
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.coordination._dependency_gate import awaits_dispatch
from synthorg.engine.coordination._wave_parking import GatedWave, gate_wave
from synthorg.engine.coordination.assignment_writer import AssignmentWriter
from synthorg.engine.coordination.models import CoordinationPhaseResult
from synthorg.engine.parallel_models import AgentAssignment, ParallelExecutionGroup
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import TaskMutationResult
from tests._shared import FakeClock, as_uuid, mock_of

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


def _task(label: str, *, status: TaskStatus = TaskStatus.CREATED) -> Task:
    return Task(
        id=as_uuid(label),
        title=f"Task {label}",
        description="A detailed test task description",
        type=TaskType.DEVELOPMENT,
        project="test-project",
        created_by="test-creator",
        status=status,
        # The model requires an assignee for any status that implies somebody
        # took the work, and every settled status here does.
        assigned_to=None if status is TaskStatus.CREATED else str(as_uuid("worker")),
    )


def _assignment(label: str) -> AgentAssignment:
    return AgentAssignment(task=_task(label), identity=_identity(f"agent-{label}"))


def _group(*assignments: AgentAssignment) -> ParallelExecutionGroup:
    return ParallelExecutionGroup(
        group_id=NotBlankStr("wave-0"),
        assignments=assignments,
        dag_level=0,
    )


def _engine(rows: dict[str, Task]) -> Any:  # type: ignore[explicit-any]
    async def _get(task_id: str) -> Task | None:
        return rows.get(task_id)

    return mock_of[TaskEngine](
        get_task=AsyncMock(side_effect=_get),
        submit=AsyncMock(
            return_value=TaskMutationResult(request_id="r", success=True, version=2)
        ),
    )


async def _gate(
    group: ParallelExecutionGroup,
    rows: dict[str, Task],
    *,
    phases: list[CoordinationPhaseResult],
) -> GatedWave:
    clock: Clock = FakeClock()
    return await gate_wave(
        group,
        wave_idx=0,
        assignment_writer=AssignmentWriter(_engine(rows)),
        dependencies={},
        clock=clock,
        start=clock.monotonic(),
        phases=phases,
    )


class TestAwaitsDispatch:
    """The rule itself, stated over statuses rather than over a run."""

    @pytest.mark.parametrize(
        "status",
        [
            TaskStatus.CREATED,
            TaskStatus.ASSIGNED,
            TaskStatus.IN_PROGRESS,
            TaskStatus.INTERRUPTED,
        ],
    )
    def test_a_subtask_without_an_outcome_still_dispatches(
        self, status: TaskStatus
    ) -> None:
        assert awaits_dispatch(status)

    @pytest.mark.parametrize(
        "status",
        [
            TaskStatus.COMPLETED,
            TaskStatus.IN_REVIEW,
            TaskStatus.FAILED,
            TaskStatus.REJECTED,
            TaskStatus.CANCELLED,
            TaskStatus.BLOCKED,
        ],
    )
    def test_a_subtask_whose_outcome_exists_does_not(self, status: TaskStatus) -> None:
        # Each of these belongs to somebody: delivered, or the replan's
        # question, or parked with a reason naming what it waits on. Running
        # it again spends a turn budget to overwrite an answer that exists.
        assert not awaits_dispatch(status)

    def test_a_missing_row_still_reaches_the_writer(self) -> None:
        # Dropping it here would read as "its work is done"; the writer fails
        # on it by id instead, which is the diagnosis.
        assert awaits_dispatch(None)


class TestGateWaveSkipsSettledWork:
    async def test_a_delivered_subtask_is_not_re_dispatched(self) -> None:
        done = _assignment("done")
        todo = _assignment("todo")
        rows = {
            str(done.task.id): _task("done", status=TaskStatus.COMPLETED),
            str(todo.task.id): _task("todo"),
        }
        phases: list[CoordinationPhaseResult] = []
        outcome = await _gate(_group(done, todo), rows, phases=phases)
        assert outcome.group is not None
        dispatched = {str(a.task.id) for a in outcome.group.assignments}
        assert dispatched == {str(todo.task.id)}
        assert outcome.settled == 1

    async def test_a_wave_already_delivered_is_not_a_failure(self) -> None:
        # The whole point of the distinction: a resumed plan must not fail on
        # the levels it had already finished.
        done = _assignment("done")
        rows = {str(done.task.id): _task("done", status=TaskStatus.COMPLETED)}
        phases: list[CoordinationPhaseResult] = []
        outcome = await _gate(_group(done), rows, phases=phases)
        assert outcome.group is None
        assert outcome.delivered
        assert len(phases) == 1
        assert phases[0].success
        assert phases[0].error is None

    async def test_a_wave_emptied_by_dead_inputs_still_fails(self) -> None:
        # The regression guard on the other side: narrowing must not turn a
        # level that did not deliver into a phase list claiming it did, which
        # is what lets a rollup read a dead run as still working.
        blocked = _assignment("blocked")
        rows = {str(blocked.task.id): _task("blocked")}
        phases: list[CoordinationPhaseResult] = []
        writer = AssignmentWriter(_engine(rows))
        clock: Clock = FakeClock()
        outcome = await gate_wave(
            _group(blocked),
            wave_idx=0,
            assignment_writer=writer,
            # Declares a dependency the engine holds no row for, which is the
            # gate's definition of "did not deliver".
            dependencies={str(blocked.task.id): ("missing-dependency",)},
            clock=clock,
            start=clock.monotonic(),
            phases=phases,
        )
        assert outcome.group is None
        assert not outcome.delivered
        assert len(phases) == 1
        assert not phases[0].success
        assert phases[0].error is not None

    async def test_a_fresh_wave_is_unchanged(self) -> None:
        # Every subtask of a plan dispatched for the first time sits at
        # CREATED, so the narrowing must be invisible on a fresh run.
        first = _assignment("first")
        second = _assignment("second")
        rows = {
            str(first.task.id): _task("first"),
            str(second.task.id): _task("second"),
        }
        phases: list[CoordinationPhaseResult] = []
        outcome = await _gate(_group(first, second), rows, phases=phases)
        assert outcome.group is not None
        assert len(outcome.group.assignments) == 2
        assert outcome.settled == 0
        assert phases == []
