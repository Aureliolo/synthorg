"""Tests for decomposition service."""

import pytest

from ai_company.core.enums import Priority, TaskStatus, TaskStructure, TaskType
from ai_company.core.task import Task
from ai_company.engine.decomposition.classifier import TaskStructureClassifier
from ai_company.engine.decomposition.manual import ManualDecompositionStrategy
from ai_company.engine.decomposition.models import (
    DecompositionContext,
    DecompositionPlan,
    SubtaskDefinition,
)
from ai_company.engine.decomposition.service import DecompositionService


def _make_task(
    task_id: str = "task-svc-1",
    *,
    task_structure: TaskStructure | None = None,
) -> Task:
    """Helper to create a minimal task."""
    return Task(
        id=task_id,
        title="Service Test Task",
        description="A task for service testing",
        type=TaskType.DEVELOPMENT,
        priority=Priority.HIGH,
        project="proj-1",
        created_by="creator",
        task_structure=task_structure,
    )


def _make_plan(
    parent_task_id: str = "task-svc-1",
) -> DecompositionPlan:
    """Helper to create a plan with dependencies."""
    return DecompositionPlan(
        parent_task_id=parent_task_id,
        subtasks=(
            SubtaskDefinition(
                id="sub-1",
                title="Setup",
                description="Initialize environment",
                required_skills=("python",),
            ),
            SubtaskDefinition(
                id="sub-2",
                title="Build",
                description="Build the feature",
                dependencies=("sub-1",),
                required_skills=("python", "sql"),
            ),
            SubtaskDefinition(
                id="sub-3",
                title="Test",
                description="Write tests",
                dependencies=("sub-2",),
                required_skills=("python", "testing"),
            ),
        ),
    )


class TestDecompositionService:
    """Tests for DecompositionService."""

    @pytest.mark.unit
    async def test_decompose_creates_tasks(self) -> None:
        """Service creates Task objects from subtask definitions."""
        task = _make_task()
        plan = _make_plan()
        strategy = ManualDecompositionStrategy(plan)
        classifier = TaskStructureClassifier()
        service = DecompositionService(strategy, classifier)
        ctx = DecompositionContext()

        result = await service.decompose_task(task, ctx)

        assert len(result.created_tasks) == 3
        for child_task in result.created_tasks:
            assert child_task.parent_task_id == task.id
            assert child_task.status == TaskStatus.CREATED
            assert child_task.assigned_to is None
            assert child_task.project == task.project
            assert child_task.created_by == task.created_by

    @pytest.mark.unit
    async def test_decompose_builds_edges(self) -> None:
        """Service builds dependency edges from subtask definitions."""
        task = _make_task()
        plan = _make_plan()
        strategy = ManualDecompositionStrategy(plan)
        classifier = TaskStructureClassifier()
        service = DecompositionService(strategy, classifier)
        ctx = DecompositionContext()

        result = await service.decompose_task(task, ctx)

        # sub-1 -> sub-2, sub-2 -> sub-3
        assert ("sub-1", "sub-2") in result.dependency_edges
        assert ("sub-2", "sub-3") in result.dependency_edges
        assert len(result.dependency_edges) == 2

    @pytest.mark.unit
    async def test_decompose_preserves_delegation_chain(self) -> None:
        """Subtasks inherit parent's delegation chain."""
        task = Task(
            id="task-svc-1",
            title="Delegated Task",
            description="Task with delegation chain",
            type=TaskType.DEVELOPMENT,
            priority=Priority.MEDIUM,
            project="proj-1",
            created_by="creator",
            delegation_chain=("agent-a", "agent-b"),
        )
        plan = DecompositionPlan(
            parent_task_id=task.id,
            subtasks=(
                SubtaskDefinition(id="sub-1", title="Child", description="Child task"),
            ),
        )
        strategy = ManualDecompositionStrategy(plan)
        classifier = TaskStructureClassifier()
        service = DecompositionService(strategy, classifier)
        ctx = DecompositionContext()

        result = await service.decompose_task(task, ctx)

        assert result.created_tasks[0].delegation_chain == ("agent-a", "agent-b")

    @pytest.mark.unit
    async def test_decompose_classifies_structure(self) -> None:
        """Service uses classifier to determine task structure."""
        task = _make_task(task_structure=TaskStructure.PARALLEL)
        plan = _make_plan()
        strategy = ManualDecompositionStrategy(plan)
        classifier = TaskStructureClassifier()
        service = DecompositionService(strategy, classifier)
        ctx = DecompositionContext()

        result = await service.decompose_task(task, ctx)

        assert result.plan.task_structure == TaskStructure.PARALLEL

    @pytest.mark.unit
    def test_rollup_status_delegates(self) -> None:
        """rollup_status delegates to StatusRollup.compute."""
        plan = _make_plan()
        strategy = ManualDecompositionStrategy(plan)
        classifier = TaskStructureClassifier()
        service = DecompositionService(strategy, classifier)

        rollup = service.rollup_status(
            "task-svc-1",
            (TaskStatus.COMPLETED, TaskStatus.COMPLETED, TaskStatus.COMPLETED),
        )
        assert rollup.derived_parent_status == TaskStatus.COMPLETED
