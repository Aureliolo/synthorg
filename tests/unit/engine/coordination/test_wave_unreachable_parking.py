"""Every subtask of a plan reaches an owner, including the ones no wave holds.

The invariant: a plan concludes only when every one of its rows becomes
terminal, so a row that no dispatcher will run must be parked with a reason.
Three of the four parking faces reason about waves that exist. The wave
BUILDER makes an earlier decision nobody hears: a subtask it cannot place with
an agent is dropped, and so is everything transitively standing on it, into a
set local to the build. Those rows appear in no group, so no gate narrows
them, no stop abandons them and no raise strands them.

Stated over the shape rather than over a run: whatever the builder's reason
for dropping a subtask, a plan id that reaches no wave reaches this park.
"""

from datetime import date
from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.clock import Clock
from synthorg.core.task import Task
from synthorg.core.task_enums import BlockedReason, TaskStatus, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.coordination._wave_execution import execute_waves
from synthorg.engine.coordination._wave_parking import abandon_unreachable
from synthorg.engine.coordination.assignment_writer import AssignmentWriter
from synthorg.engine.coordination.models import CoordinationWave
from synthorg.engine.parallel_models import AgentAssignment, ParallelExecutionGroup
from synthorg.engine.parallel_protocol import ParallelExecutorProtocol
from synthorg.engine.task_engine import TaskEngine
from synthorg.engine.task_engine_models import (
    TaskMutationResult,
    TransitionTaskMutation,
)
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


class _Engine:
    """A task engine double that records the parks it was asked for."""

    def __init__(self, rows: dict[str, Task]) -> None:
        self.rows = rows
        self.submitted: list[TransitionTaskMutation] = []

    async def get_task(self, task_id: str) -> Task | None:
        return self.rows.get(task_id)

    async def submit(self, mutation: TransitionTaskMutation) -> TaskMutationResult:
        self.submitted.append(mutation)
        return TaskMutationResult(request_id=mutation.request_id, success=True)


def _engine(rows: dict[str, Task]) -> Any:  # type: ignore[explicit-any]
    double = _Engine(rows)
    return mock_of[TaskEngine](
        get_task=AsyncMock(side_effect=double.get_task),
        submit=AsyncMock(side_effect=double.submit),
    )


def _parks(engine: Any) -> list[TransitionTaskMutation]:  # type: ignore[explicit-any]
    return [call.args[0] for call in engine.submit.await_args_list]


class TestAbandonUnreachable:
    """The rule itself, over ids rather than over a run."""

    async def test_parks_a_subtask_no_wave_holds(self) -> None:
        scheduled = _assignment("scheduled")
        orphan = _task("orphan")
        engine = _engine(
            {
                str(scheduled.task.id): _task("scheduled"),
                str(orphan.id): orphan,
            }
        )
        await abandon_unreachable(
            (_group(scheduled),),
            subtask_ids=[str(scheduled.task.id), str(orphan.id)],
            writer=AssignmentWriter(engine),
        )
        parks = _parks(engine)
        assert [p.task_id for p in parks] == [str(orphan.id)]
        assert parks[0].target_status is TaskStatus.BLOCKED
        assert parks[0].overrides["blocked_reason"] is BlockedReason.DEPENDENCY_FAILED

    async def test_says_on_the_row_that_no_wave_was_built(self) -> None:
        # The reason is what an operator acts on without the log, and this
        # park's cause is not any other park's: nothing failed and no run
        # stopped short of it.
        orphan = _task("orphan")
        engine = _engine({str(orphan.id): orphan})
        await abandon_unreachable(
            (),
            subtask_ids=[str(orphan.id)],
            writer=AssignmentWriter(engine),
        )
        assert "no wave could be built" in _parks(engine)[0].reason

    async def test_leaves_a_subtask_a_wave_holds_alone(self) -> None:
        scheduled = _assignment("scheduled")
        engine = _engine({str(scheduled.task.id): _task("scheduled")})
        await abandon_unreachable(
            (_group(scheduled),),
            subtask_ids=[str(scheduled.task.id)],
            writer=AssignmentWriter(engine),
        )
        assert _parks(engine) == []

    @pytest.mark.parametrize(
        "status",
        [
            TaskStatus.COMPLETED,
            TaskStatus.FAILED,
            TaskStatus.BLOCKED,
            TaskStatus.IN_REVIEW,
        ],
    )
    async def test_leaves_a_row_that_already_has_an_outcome(
        self, status: TaskStatus
    ) -> None:
        # A row that ran owns its result, and a row already parked keeps the
        # reason naming its ACTUAL dependency, which is more specific than
        # "no wave was built". Re-asserting BLOCKED is also refused by the
        # state machine, which is how this fired every cadence for ever.
        settled = _task("settled", status=status)
        engine = _engine({str(settled.id): settled})
        await abandon_unreachable(
            (),
            subtask_ids=[str(settled.id)],
            writer=AssignmentWriter(engine),
        )
        assert _parks(engine) == []

    async def test_a_row_the_engine_does_not_hold_is_not_invented(self) -> None:
        engine = _engine({})
        await abandon_unreachable(
            (),
            subtask_ids=[str(as_uuid("never-filed"))],
            writer=AssignmentWriter(engine),
        )
        assert _parks(engine) == []


class TestExecuteWavesParksWhatItWasNeverGiven:
    """The loop reaches the rule, so no dispatcher has to remember to."""

    async def test_a_plan_row_absent_from_every_wave_is_parked(self) -> None:
        # The live shape: routing could not place one subtask, so the builder
        # dropped it and everything standing on it, and the loop received
        # waves that never mention them.
        scheduled = _assignment("scheduled")
        dropped = _task("dropped")
        engine = _engine(
            {
                str(scheduled.task.id): _task("scheduled"),
                str(dropped.id): dropped,
            }
        )
        clock: Clock = FakeClock()
        waves: list[CoordinationWave] = []
        await execute_waves(
            (_group(scheduled),),
            mock_of[ParallelExecutorProtocol](
                execute_group=AsyncMock(side_effect=RuntimeError("not dispatched here"))
            ),
            clock=clock,
            fail_fast=True,
            assignment_writer=AssignmentWriter(engine),
            waves=waves,
            # The plan's own ids: the dependency map keys ARE the subtask set,
            # which is why the loop needs nothing new from the builder.
            dependencies={
                str(scheduled.task.id): (),
                str(dropped.id): (str(as_uuid("unplaceable")),),
            },
        )
        parked = [
            p.task_id
            for p in _parks(engine)
            if p.overrides.get("blocked_reason") is BlockedReason.DEPENDENCY_FAILED
        ]
        assert str(dropped.id) in parked
