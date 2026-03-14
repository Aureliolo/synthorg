"""Tests for QueueReturnStrategy."""

import pytest

from synthorg.core.enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.hr.queue_return_strategy import QueueReturnStrategy
from tests.unit.hr.conftest import make_task


@pytest.mark.unit
class TestQueueReturnStrategy:
    """QueueReturnStrategy.reassign tests."""

    async def test_assigned_task_becomes_interrupted(self) -> None:
        strategy = QueueReturnStrategy()
        task = make_task(
            task_id="task-001",
            status=TaskStatus.ASSIGNED,
            assigned_to="agent-001",
        )
        result = await strategy.reassign(
            agent_id=NotBlankStr("agent-001"),
            active_tasks=(task,),
        )
        assert len(result) == 1
        assert result[0].status == TaskStatus.INTERRUPTED
        assert result[0].assigned_to is None

    async def test_in_progress_task_becomes_interrupted(self) -> None:
        strategy = QueueReturnStrategy()
        task = make_task(
            task_id="task-002",
            status=TaskStatus.IN_PROGRESS,
            assigned_to="agent-001",
        )
        result = await strategy.reassign(
            agent_id=NotBlankStr("agent-001"),
            active_tasks=(task,),
        )
        assert len(result) == 1
        assert result[0].status == TaskStatus.INTERRUPTED

    async def test_empty_list_returns_empty(self) -> None:
        strategy = QueueReturnStrategy()
        result = await strategy.reassign(
            agent_id=NotBlankStr("agent-001"),
            active_tasks=(),
        )
        assert result == ()

    async def test_already_interrupted_skipped(self) -> None:
        strategy = QueueReturnStrategy()
        task = make_task(
            task_id="task-003",
            status=TaskStatus.INTERRUPTED,
        )
        result = await strategy.reassign(
            agent_id=NotBlankStr("agent-001"),
            active_tasks=(task,),
        )
        assert result == ()

    async def test_multiple_tasks_mixed_statuses(self) -> None:
        strategy = QueueReturnStrategy()
        assigned = make_task(
            task_id="task-a",
            status=TaskStatus.ASSIGNED,
            assigned_to="agent-001",
        )
        in_progress = make_task(
            task_id="task-b",
            status=TaskStatus.IN_PROGRESS,
            assigned_to="agent-001",
        )
        interrupted = make_task(
            task_id="task-c",
            status=TaskStatus.INTERRUPTED,
        )
        result = await strategy.reassign(
            agent_id=NotBlankStr("agent-001"),
            active_tasks=(assigned, in_progress, interrupted),
        )
        assert len(result) == 2
        statuses = {t.status for t in result}
        assert statuses == {TaskStatus.INTERRUPTED}

    async def test_strategy_name(self) -> None:
        strategy = QueueReturnStrategy()
        assert strategy.name == "queue_return"
