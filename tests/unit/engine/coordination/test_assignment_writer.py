"""A wave's assignments are persisted before the wave dispatches."""

from datetime import date
from typing import Any
from unittest.mock import AsyncMock

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus, TaskType
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
        model=ModelConfig(provider="test-provider", model_id="test-small-001"),
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


def _group(*assignments: AgentAssignment) -> ParallelExecutionGroup:
    return ParallelExecutionGroup(
        group_id=NotBlankStr("wave-0"),
        assignments=assignments,
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
