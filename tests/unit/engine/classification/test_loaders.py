"""Tests for scoped context loaders."""

from datetime import date
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from synthorg.budget.coordination_config import DetectionScope
from synthorg.core.agent import AgentIdentity, ModelConfig
from synthorg.core.enums import Complexity, Priority, TaskStatus, TaskType
from synthorg.core.task import Task
from synthorg.engine.classification.loaders import SameTaskLoader, TaskTreeLoader
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import (
    ExecutionResult,
    TerminationReason,
)
from synthorg.persistence.task_protocol import TaskRepository


def _identity() -> AgentIdentity:
    return AgentIdentity(
        id=uuid4(),
        name="Test Agent",
        role="Developer",
        department="Engineering",
        model=ModelConfig(
            provider="test-provider",
            model_id="test-small-001",
        ),
        hiring_date=date(2026, 1, 1),
    )


def _execution_result() -> ExecutionResult:
    identity = _identity()
    ctx = AgentContext.from_identity(identity)
    return ExecutionResult(
        context=ctx,
        termination_reason=TerminationReason.COMPLETED,
    )


def _task(
    *,
    task_id: str = "task-1",
    parent_task_id: str | None = None,
    assigned_to: str | None = "agent-2",
    delegation_chain: tuple[str, ...] = (),
    description: str = "Test task",
) -> Task:
    return Task(
        id=task_id,
        title="Test",
        description=description,
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="test-project",
        created_by="agent-1",
        assigned_to=assigned_to,
        status=TaskStatus.IN_PROGRESS if assigned_to else TaskStatus.CREATED,
        parent_task_id=parent_task_id,
        delegation_chain=delegation_chain,
        estimated_complexity=Complexity.MEDIUM,
    )


@pytest.mark.unit
class TestSameTaskLoader:
    """SameTaskLoader produces SAME_TASK context."""

    async def test_load_wraps_execution_result(self) -> None:
        loader = SameTaskLoader()
        er = _execution_result()
        ctx = await loader.load(er, "agent-1", "task-1")

        assert ctx.scope == DetectionScope.SAME_TASK
        assert ctx.agent_id == "agent-1"
        assert ctx.task_id == "task-1"
        assert ctx.execution_result is er
        assert ctx.delegate_executions == ()
        assert ctx.review_results == ()
        assert ctx.delegation_requests == ()


@pytest.mark.unit
class TestTaskTreeLoader:
    """TaskTreeLoader queries child tasks and builds context."""

    async def test_load_with_no_children(self) -> None:
        repo = AsyncMock(spec=TaskRepository)
        repo.list_tasks = AsyncMock(return_value=())

        loader = TaskTreeLoader(task_repo=repo)
        er = _execution_result()
        ctx = await loader.load(er, "agent-1", "task-1")

        assert ctx.scope == DetectionScope.TASK_TREE
        assert ctx.delegation_requests == ()
        repo.list_tasks.assert_awaited_once()

    async def test_load_with_child_tasks(self) -> None:
        child = _task(
            task_id="child-1",
            parent_task_id="task-1",
            assigned_to="agent-2",
            delegation_chain=("agent-1",),
            description="Child task description",
        )
        repo = AsyncMock(spec=TaskRepository)
        repo.list_tasks = AsyncMock(return_value=(child,))

        loader = TaskTreeLoader(task_repo=repo)
        er = _execution_result()
        ctx = await loader.load(er, "agent-1", "task-1")

        assert ctx.scope == DetectionScope.TASK_TREE
        assert len(ctx.delegation_requests) == 1
        req = ctx.delegation_requests[0]
        assert req.delegator_id == "agent-1"
        assert req.delegatee_id == "agent-2"

    async def test_load_survives_repo_failure(self) -> None:
        """Repository failure produces empty delegation requests."""
        repo = AsyncMock(spec=TaskRepository)
        repo.list_tasks = AsyncMock(
            side_effect=RuntimeError("connection lost"),
        )

        loader = TaskTreeLoader(task_repo=repo)
        er = _execution_result()
        ctx = await loader.load(er, "agent-1", "task-1")

        assert ctx.scope == DetectionScope.TASK_TREE
        assert ctx.delegation_requests == ()

    async def test_load_memory_error_propagates(self) -> None:
        """MemoryError from repository propagates."""
        repo = AsyncMock(spec=TaskRepository)
        repo.list_tasks = AsyncMock(side_effect=MemoryError)

        loader = TaskTreeLoader(task_repo=repo)
        er = _execution_result()
        with pytest.raises(MemoryError):
            await loader.load(er, "agent-1", "task-1")

    async def test_sanitizes_description(self) -> None:
        """Child task descriptions are sanitized."""
        child = _task(
            task_id="child-1",
            parent_task_id="task-1",
            description="Visit https://secret.example.com/admin",
        )
        repo = AsyncMock(spec=TaskRepository)
        repo.list_tasks = AsyncMock(return_value=(child,))

        loader = TaskTreeLoader(task_repo=repo)
        er = _execution_result()
        ctx = await loader.load(er, "agent-1", "task-1")

        assert len(ctx.delegation_requests) == 1
        # URL should be redacted by sanitize_message
        assert "secret.example.com" not in ctx.delegation_requests[0].refinement
