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
from ai_company.engine.errors import DecompositionCycleError


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

        assert result.created_tasks[0].delegation_chain == (
            "agent-a",
            "agent-b",
        )

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
    async def test_decompose_structure_override(self) -> None:
        """Classifier overrides plan's default structure when it differs."""
        # Task with sequential signals (dependencies + no parallel keywords)
        task = Task(
            id="task-svc-1",
            title="Service Test Task",
            description="A task for service testing",
            type=TaskType.DEVELOPMENT,
            priority=Priority.HIGH,
            project="proj-1",
            created_by="creator",
            dependencies=("dep-1",),
        )
        # Plan defaults to SEQUENTIAL, but classifier should also return
        # SEQUENTIAL based on dependencies — so they agree.
        # Use a plan with PARALLEL structure to test override.
        plan = DecompositionPlan(
            parent_task_id=task.id,
            subtasks=(SubtaskDefinition(id="sub-1", title="A", description="Desc A"),),
            task_structure=TaskStructure.PARALLEL,
        )
        strategy = ManualDecompositionStrategy(plan)
        classifier = TaskStructureClassifier()
        service = DecompositionService(strategy, classifier)
        ctx = DecompositionContext()

        result = await service.decompose_task(task, ctx)

        # Classifier infers SEQUENTIAL (dependencies present, no parallel
        # language), overriding the plan's PARALLEL
        assert result.plan.task_structure == TaskStructure.SEQUENTIAL

    @pytest.mark.unit
    async def test_decompose_dag_cycle_raises(self) -> None:
        """Service raises DecompositionCycleError for cyclic plans."""
        task = _make_task()
        # Cycle: sub-1 -> sub-2 -> sub-1
        plan = DecompositionPlan(
            parent_task_id=task.id,
            subtasks=(
                SubtaskDefinition(
                    id="sub-1",
                    title="A",
                    description="Desc A",
                    dependencies=("sub-2",),
                ),
                SubtaskDefinition(
                    id="sub-2",
                    title="B",
                    description="Desc B",
                    dependencies=("sub-1",),
                ),
            ),
        )
        strategy = ManualDecompositionStrategy(plan)
        classifier = TaskStructureClassifier()
        service = DecompositionService(strategy, classifier)
        ctx = DecompositionContext()

        with pytest.raises(DecompositionCycleError, match="cycle"):
            await service.decompose_task(task, ctx)

    @pytest.mark.unit
    async def test_decompose_uses_subtask_complexity(self) -> None:
        """Child tasks use subtask's estimated_complexity, not parent's."""
        from ai_company.core.enums import Complexity

        task = Task(
            id="task-svc-1",
            title="Epic Task",
            description="Parent task",
            type=TaskType.DEVELOPMENT,
            priority=Priority.HIGH,
            project="proj-1",
            created_by="creator",
            estimated_complexity=Complexity.EPIC,
        )
        plan = DecompositionPlan(
            parent_task_id=task.id,
            subtasks=(
                SubtaskDefinition(
                    id="sub-1",
                    title="Simple Child",
                    description="Simple subtask",
                    estimated_complexity=Complexity.SIMPLE,
                ),
            ),
        )
        strategy = ManualDecompositionStrategy(plan)
        classifier = TaskStructureClassifier()
        service = DecompositionService(strategy, classifier)
        ctx = DecompositionContext()

        result = await service.decompose_task(task, ctx)

        assert result.created_tasks[0].estimated_complexity == Complexity.SIMPLE

    @pytest.mark.unit
    async def test_decompose_exception_propagates(self) -> None:
        """Unexpected exceptions are logged and re-raised."""

        class _FailingStrategy:
            async def decompose(
                self, task: Task, context: DecompositionContext
            ) -> DecompositionPlan:
                msg = "strategy boom"
                raise RuntimeError(msg)

            def get_strategy_name(self) -> str:
                return "failing"

        task = _make_task()
        strategy = _FailingStrategy()
        classifier = TaskStructureClassifier()
        service = DecompositionService(strategy, classifier)
        ctx = DecompositionContext()

        with pytest.raises(RuntimeError, match="strategy boom"):
            await service.decompose_task(task, ctx)

    @pytest.mark.unit
    async def test_decompose_propagates_dependencies(self) -> None:
        """Subtask dependencies propagate to created Task objects."""
        task = _make_task()
        plan = _make_plan()
        strategy = ManualDecompositionStrategy(plan)
        classifier = TaskStructureClassifier()
        service = DecompositionService(strategy, classifier)
        ctx = DecompositionContext()

        result = await service.decompose_task(task, ctx)

        tasks_by_id = {t.id: t for t in result.created_tasks}
        assert tasks_by_id["sub-1"].dependencies == ()
        assert tasks_by_id["sub-2"].dependencies == ("sub-1",)
        assert tasks_by_id["sub-3"].dependencies == ("sub-2",)

    @pytest.mark.unit
    def test_rollup_status_delegates(self) -> None:
        """rollup_status delegates to StatusRollup.compute."""
        plan = _make_plan()
        strategy = ManualDecompositionStrategy(plan)
        classifier = TaskStructureClassifier()
        service = DecompositionService(strategy, classifier)

        rollup = service.rollup_status(
            "task-svc-1",
            (
                TaskStatus.COMPLETED,
                TaskStatus.COMPLETED,
                TaskStatus.COMPLETED,
            ),
        )
        assert rollup.derived_parent_status == TaskStatus.COMPLETED
