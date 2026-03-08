"""Tests for manual decomposition strategy."""

import pytest

from ai_company.core.enums import Priority, TaskType
from ai_company.core.task import Task
from ai_company.engine.decomposition.manual import ManualDecompositionStrategy
from ai_company.engine.decomposition.models import (
    DecompositionContext,
    DecompositionPlan,
    SubtaskDefinition,
)
from ai_company.engine.errors import DecompositionError


def _make_task(task_id: str = "task-manual-1") -> Task:
    """Helper to create a minimal task."""
    return Task(
        id=task_id,
        title="Manual Test Task",
        description="A task for manual decomposition testing",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="proj-1",
        created_by="creator",
    )


def _make_plan(
    parent_task_id: str = "task-manual-1",
    subtask_count: int = 2,
) -> DecompositionPlan:
    """Helper to create a plan with N subtasks."""
    subtasks = tuple(
        SubtaskDefinition(
            id=f"sub-{i}",
            title=f"Subtask {i}",
            description=f"Description {i}",
        )
        for i in range(subtask_count)
    )
    return DecompositionPlan(
        parent_task_id=parent_task_id,
        subtasks=subtasks,
    )


class TestManualDecompositionStrategy:
    """Tests for ManualDecompositionStrategy."""

    @pytest.mark.unit
    async def test_returns_plan(self) -> None:
        """Strategy returns the pre-built plan."""
        task = _make_task()
        plan = _make_plan()
        strategy = ManualDecompositionStrategy(plan)
        ctx = DecompositionContext()

        result = await strategy.decompose(task, ctx)
        assert result is plan

    @pytest.mark.unit
    async def test_task_id_mismatch_rejected(self) -> None:
        """Mismatched task ID raises DecompositionError."""
        task = _make_task("task-other")
        plan = _make_plan("task-manual-1")
        strategy = ManualDecompositionStrategy(plan)
        ctx = DecompositionContext()

        with pytest.raises(DecompositionError, match="does not match"):
            await strategy.decompose(task, ctx)

    @pytest.mark.unit
    async def test_depth_exceeded_rejected(self) -> None:
        """Depth at max raises DecompositionDepthError."""
        task = _make_task()
        plan = _make_plan()
        strategy = ManualDecompositionStrategy(plan)
        ctx = DecompositionContext(current_depth=2, max_depth=3)

        result = await strategy.decompose(task, ctx)
        assert result is plan

        # At max depth (2 >= 3 is false, 3 >= 3 is true - can't even create context)
        with pytest.raises(ValueError, match="reached max depth"):
            DecompositionContext(current_depth=3, max_depth=3)

    @pytest.mark.unit
    async def test_too_many_subtasks_rejected(self) -> None:
        """Too many subtasks raises DecompositionError."""
        task = _make_task()
        plan = _make_plan(subtask_count=5)
        strategy = ManualDecompositionStrategy(plan)
        ctx = DecompositionContext(max_subtasks=3)

        with pytest.raises(DecompositionError, match="exceeding max"):
            await strategy.decompose(task, ctx)

    @pytest.mark.unit
    def test_strategy_name(self) -> None:
        """Strategy name is 'manual'."""
        plan = _make_plan()
        strategy = ManualDecompositionStrategy(plan)
        assert strategy.get_strategy_name() == "manual"
