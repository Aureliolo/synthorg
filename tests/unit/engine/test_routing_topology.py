"""Tests for topology selector."""

import pytest

from synthorg.core.artifact import ExpectedArtifact
from synthorg.core.enums import (
    ArtifactType,
    CoordinationTopology,
    Priority,
    TaskStructure,
    TaskType,
)
from synthorg.core.task import Task
from synthorg.engine.decomposition.models import (
    DecompositionPlan,
    SubtaskDefinition,
)
from synthorg.engine.routing.models import AutoTopologyConfig
from synthorg.engine.routing.topology_selector import TopologySelector


def _make_task(
    *,
    coordination_topology: CoordinationTopology = CoordinationTopology.AUTO,
    artifact_count: int = 0,
) -> Task:
    """Helper to create a task with optional topology override."""
    artifacts = tuple(
        ExpectedArtifact(
            type=ArtifactType.CODE,
            path=f"src/artifact_{i}.py",
        )
        for i in range(artifact_count)
    )
    return Task(
        id="task-topo-1",
        title="Topology Test",
        description="Testing topology selection",
        type=TaskType.DEVELOPMENT,
        priority=Priority.MEDIUM,
        project="proj-1",
        created_by="creator",
        coordination_topology=coordination_topology,
        artifacts_expected=artifacts,
    )


def _make_plan(
    structure: TaskStructure = TaskStructure.SEQUENTIAL,
) -> DecompositionPlan:
    """Helper to create a plan with given structure."""
    return DecompositionPlan(
        parent_task_id="task-topo-1",
        subtasks=(SubtaskDefinition(id="sub-1", title="A", description="A desc"),),
        task_structure=structure,
    )


class TestTopologySelector:
    """Tests for TopologySelector."""

    @pytest.mark.unit
    def test_explicit_override(self) -> None:
        """Explicit topology on task overrides auto-selection."""
        selector = TopologySelector()
        task = _make_task(coordination_topology=CoordinationTopology.DECENTRALIZED)
        plan = _make_plan()

        result = selector.select(task, plan)
        assert result == CoordinationTopology.DECENTRALIZED

    @pytest.mark.unit
    def test_sequential_structure(self) -> None:
        """Sequential structure -> SAS (default)."""
        selector = TopologySelector()
        task = _make_task()
        plan = _make_plan(TaskStructure.SEQUENTIAL)

        result = selector.select(task, plan)
        assert result == CoordinationTopology.SAS

    @pytest.mark.unit
    def test_parallel_structure_low_tools(self) -> None:
        """Parallel structure with few tools -> CENTRALIZED."""
        selector = TopologySelector()
        task = _make_task(artifact_count=2)
        plan = _make_plan(TaskStructure.PARALLEL)

        result = selector.select(task, plan)
        assert result == CoordinationTopology.CENTRALIZED

    @pytest.mark.unit
    def test_parallel_structure_high_tools(self) -> None:
        """Parallel structure with many tools -> DECENTRALIZED."""
        selector = TopologySelector()
        task = _make_task(artifact_count=6)
        plan = _make_plan(TaskStructure.PARALLEL)

        result = selector.select(task, plan)
        assert result == CoordinationTopology.DECENTRALIZED

    @pytest.mark.unit
    def test_mixed_structure(self) -> None:
        """Mixed structure -> CONTEXT_DEPENDENT (default)."""
        selector = TopologySelector()
        task = _make_task()
        plan = _make_plan(TaskStructure.MIXED)

        result = selector.select(task, plan)
        assert result == CoordinationTopology.CONTEXT_DEPENDENT

    @pytest.mark.unit
    def test_custom_config(self) -> None:
        """Custom config overrides default topology selection."""
        config = AutoTopologyConfig(
            sequential_override=CoordinationTopology.CENTRALIZED,
            parallel_default=CoordinationTopology.DECENTRALIZED,
            mixed_default=CoordinationTopology.SAS,
        )
        selector = TopologySelector(config)
        task = _make_task()

        # Sequential -> CENTRALIZED (custom)
        plan_seq = _make_plan(TaskStructure.SEQUENTIAL)
        assert selector.select(task, plan_seq) == CoordinationTopology.CENTRALIZED

        # Mixed -> SAS (custom)
        plan_mix = _make_plan(TaskStructure.MIXED)
        assert selector.select(task, plan_mix) == CoordinationTopology.SAS

    @pytest.mark.unit
    def test_parallel_structure_at_threshold(self) -> None:
        """Parallel structure at exact threshold -> CENTRALIZED (not DECENTRALIZED)."""
        selector = TopologySelector()
        task = _make_task(artifact_count=4)
        plan = _make_plan(TaskStructure.PARALLEL)

        result = selector.select(task, plan)
        assert result == CoordinationTopology.CENTRALIZED

    @pytest.mark.unit
    def test_parallel_structure_above_threshold(self) -> None:
        """Parallel structure one above threshold -> DECENTRALIZED."""
        selector = TopologySelector()
        task = _make_task(artifact_count=5)
        plan = _make_plan(TaskStructure.PARALLEL)

        result = selector.select(task, plan)
        assert result == CoordinationTopology.DECENTRALIZED

    @pytest.mark.unit
    def test_config_property(self) -> None:
        """Config property returns the active configuration."""
        config = AutoTopologyConfig()
        selector = TopologySelector(config)
        assert selector.config is config
