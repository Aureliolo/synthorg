"""Tests for task structure classifier."""

import pytest

from synthorg.core.artifact import ExpectedArtifact
from synthorg.core.enums import (
    ArtifactType,
    Priority,
    TaskStructure,
    TaskType,
)
from synthorg.core.task import AcceptanceCriterion, Task
from synthorg.engine.decomposition.classifier import TaskStructureClassifier


def _make_task(
    description: str = "Generic task",
    *,
    criteria: tuple[AcceptanceCriterion, ...] = (),
    task_structure: TaskStructure | None = None,
    dependencies: tuple[str, ...] = (),
    artifacts: tuple[ExpectedArtifact, ...] = (),
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
        artifacts_expected=artifacts,
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
        """Parallel language signals with no sequential -> PARALLEL."""
        classifier = TaskStructureClassifier()
        task = _make_task(
            "Build frontend and backend independently in parallel",
        )
        result = classifier.classify(task)
        assert result == TaskStructure.PARALLEL

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
    def test_neutral_task_no_signals(self) -> None:
        """Task with no language signals defaults to SEQUENTIAL."""
        classifier = TaskStructureClassifier()
        task = _make_task("Do something generic")
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

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "description",
        [
            "then do something",
            "after the setup, continue",
            "before deployment, test",
            "first configure the DB",
            "next build the API",
            "finally deploy it",
            "step 1 is setup",
            "phase 2 is testing",
        ],
        ids=[
            "then",
            "after",
            "before",
            "first",
            "next",
            "finally",
            "step_N",
            "phase_N",
        ],
    )
    def test_individual_sequential_patterns(self, description: str) -> None:
        """Each sequential pattern is individually recognised."""
        classifier = TaskStructureClassifier()
        task = _make_task(description, dependencies=("dep-1",))
        result = classifier.classify(task)
        assert result == TaskStructure.SEQUENTIAL

    @pytest.mark.unit
    def test_artifact_boundary_at_threshold(self) -> None:
        """Exactly 4 artifacts favours sequential structural signal."""
        classifier = TaskStructureClassifier()
        artifacts = tuple(
            ExpectedArtifact(type=ArtifactType.CODE, path=f"file{i}.py")
            for i in range(4)
        )
        task = _make_task("Do generic work", artifacts=artifacts)
        result = classifier.classify(task)
        assert result == TaskStructure.SEQUENTIAL

    @pytest.mark.unit
    def test_artifact_boundary_above_threshold(self) -> None:
        """5 artifacts favours parallel structural signal."""
        classifier = TaskStructureClassifier()
        artifacts = tuple(
            ExpectedArtifact(type=ArtifactType.CODE, path=f"file{i}.py")
            for i in range(5)
        )
        task = _make_task("Do generic work", artifacts=artifacts)
        result = classifier.classify(task)
        assert result == TaskStructure.PARALLEL

    @pytest.mark.unit
    @pytest.mark.parametrize(
        "description",
        [
            "work independently on modules",
            "run them in parallel",
            "execute concurrently",
            "process simultaneously",
            "develop separately",
        ],
        ids=[
            "independently",
            "in_parallel",
            "concurrently",
            "simultaneously",
            "separately",
        ],
    )
    def test_individual_parallel_patterns(self, description: str) -> None:
        """Each parallel pattern is individually recognised."""
        classifier = TaskStructureClassifier()
        task = _make_task(description)
        result = classifier.classify(task)
        assert result == TaskStructure.PARALLEL
