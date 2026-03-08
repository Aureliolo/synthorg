"""Tests for task structure classifier."""

import pytest

from ai_company.core.enums import (
    Priority,
    TaskStructure,
    TaskType,
)
from ai_company.core.task import AcceptanceCriterion, Task
from ai_company.engine.decomposition.classifier import TaskStructureClassifier


def _make_task(
    description: str = "Generic task",
    *,
    criteria: tuple[AcceptanceCriterion, ...] = (),
    task_structure: TaskStructure | None = None,
    dependencies: tuple[str, ...] = (),
) -> Task:
    """Helper to create a task with custom description/criteria."""
    return Task(
        id="task-cls-1",
        title="Test Task",
        description=description,
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="proj-1",
        created_by="creator",
        acceptance_criteria=criteria,
        task_structure=task_structure,
        dependencies=dependencies,
    )


class TestTaskStructureClassifier:
    """Tests for TaskStructureClassifier."""

    @pytest.mark.unit
    def test_explicit_structure_returned(self) -> None:
        """Explicit task_structure is returned without heuristic analysis."""
        classifier = TaskStructureClassifier()
        task = _make_task(task_structure=TaskStructure.PARALLEL)
        assert classifier.classify(task) == TaskStructure.PARALLEL

    @pytest.mark.unit
    def test_sequential_signals(self) -> None:
        """Sequential language signals classify as SEQUENTIAL."""
        classifier = TaskStructureClassifier()
        task = _make_task(
            "First set up the database, then configure the API, finally deploy",
            dependencies=("dep-1",),
        )
        result = classifier.classify(task)
        assert result == TaskStructure.SEQUENTIAL

    @pytest.mark.unit
    def test_parallel_signals(self) -> None:
        """Parallel language signals classify as PARALLEL."""
        classifier = TaskStructureClassifier()
        task = _make_task(
            "Build frontend and backend independently in parallel",
        )
        result = classifier.classify(task)
        # Should have parallel signals
        assert result in (TaskStructure.PARALLEL, TaskStructure.MIXED)

    @pytest.mark.unit
    def test_mixed_signals(self) -> None:
        """Both sequential and parallel signals classify as MIXED."""
        classifier = TaskStructureClassifier()
        task = _make_task(
            "First build the API, then independently deploy frontend and backend",
            criteria=(
                AcceptanceCriterion(description="Step 1: API is built"),
                AcceptanceCriterion(description="Deploy separately and concurrently"),
            ),
        )
        result = classifier.classify(task)
        assert result == TaskStructure.MIXED

    @pytest.mark.unit
    def test_default_fallback_with_dependencies(self) -> None:
        """Task with dependencies and no parallel signals -> SEQUENTIAL."""
        classifier = TaskStructureClassifier()
        task = _make_task(
            "Do something generic",
            dependencies=("dep-1",),
        )
        result = classifier.classify(task)
        assert result == TaskStructure.SEQUENTIAL

    @pytest.mark.unit
    def test_criteria_contribute_to_scoring(self) -> None:
        """Acceptance criteria text is analyzed for signals."""
        classifier = TaskStructureClassifier()
        task = _make_task(
            "Build the system",
            criteria=(
                AcceptanceCriterion(description="Step 1 complete"),
                AcceptanceCriterion(description="After step 1, step 2 done"),
                AcceptanceCriterion(description="Finally, step 3 verified"),
            ),
            dependencies=("dep-1",),
        )
        result = classifier.classify(task)
        assert result == TaskStructure.SEQUENTIAL
