"""Tests for dependency graph utilities."""

import pytest

from synthorg.engine.decomposition.dag import DependencyGraph
from synthorg.engine.decomposition.models import SubtaskDefinition
from synthorg.engine.errors import DecompositionCycleError, DecompositionError


def _sub(
    sid: str,
    deps: tuple[str, ...] = (),
) -> SubtaskDefinition:
    """Helper to create a minimal SubtaskDefinition."""
    return SubtaskDefinition(
        id=sid,
        title=f"Task {sid}",
        description=f"Description for {sid}",
        dependencies=deps,
    )


class TestDependencyGraph:
    """Tests for DependencyGraph."""

    @pytest.mark.unit
    def test_validate_acyclic_graph(self) -> None:
        """Valid acyclic graph passes validation."""
        graph = DependencyGraph(
            (
                _sub("a"),
                _sub("b", ("a",)),
                _sub("c", ("a",)),
                _sub("d", ("b", "c")),
            )
        )
        graph.validate()  # Should not raise

    @pytest.mark.unit
    def test_validate_detects_cycle(self) -> None:
        """Cycle detection raises DecompositionCycleError."""
        graph = DependencyGraph(
            (
                _sub("a", ("b",)),
                _sub("b", ("a",)),
            )
        )
        with pytest.raises(DecompositionCycleError, match="cycle"):
            graph.validate()

    @pytest.mark.unit
    def test_validate_detects_missing_reference(self) -> None:
        """Missing dependency reference raises DecompositionError."""
        subtask = SubtaskDefinition(
            id="a",
            title="Task a",
            description="Desc",
            dependencies=("missing",),
        )
        graph = DependencyGraph((subtask,))
        with pytest.raises(DecompositionError, match="unknown dependency"):
            graph.validate()

    @pytest.mark.unit
    def test_validate_and_topo_sort_agree(self) -> None:
        """validate() and topological_sort() agree on valid graphs."""
        graph = DependencyGraph(
            (
                _sub("a"),
                _sub("b", ("a",)),
                _sub("c", ("a",)),
                _sub("d", ("b", "c")),
            )
        )
        graph.validate()
        result = graph.topological_sort()
        assert result[0] == "a"
        assert result[-1] == "d"

    @pytest.mark.unit
    def test_topological_sort_linear(self) -> None:
        """Linear chain produces correct topological order."""
        graph = DependencyGraph(
            (
                _sub("c", ("b",)),
                _sub("b", ("a",)),
                _sub("a"),
            )
        )
        assert graph.topological_sort() == ("a", "b", "c")

    @pytest.mark.unit
    def test_topological_sort_diamond(self) -> None:
        """Diamond graph produces valid topological order."""
        graph = DependencyGraph(
            (
                _sub("a"),
                _sub("b", ("a",)),
                _sub("c", ("a",)),
                _sub("d", ("b", "c")),
            )
        )
        order = graph.topological_sort()
        assert order[0] == "a"
        assert order[-1] == "d"
        assert order.index("b") < order.index("d")
        assert order.index("c") < order.index("d")

    @pytest.mark.unit
    def test_topological_sort_cycle_raises(self) -> None:
        """Topological sort raises on cycle."""
        graph = DependencyGraph(
            (
                _sub("a", ("c",)),
                _sub("b", ("a",)),
                _sub("c", ("b",)),
            )
        )
        with pytest.raises(DecompositionCycleError):
            graph.topological_sort()

    @pytest.mark.unit
    def test_parallel_groups_independent(self) -> None:
        """Independent subtasks form a single group."""
        graph = DependencyGraph(
            (
                _sub("a"),
                _sub("b"),
                _sub("c"),
            )
        )
        groups = graph.parallel_groups()
        assert len(groups) == 1
        assert set(groups[0]) == {"a", "b", "c"}

    @pytest.mark.unit
    def test_parallel_groups_sequential(self) -> None:
        """Linear chain produces one group per subtask."""
        graph = DependencyGraph(
            (
                _sub("a"),
                _sub("b", ("a",)),
                _sub("c", ("b",)),
            )
        )
        groups = graph.parallel_groups()
        assert len(groups) == 3
        assert groups[0] == ("a",)
        assert groups[1] == ("b",)
        assert groups[2] == ("c",)

    @pytest.mark.unit
    def test_parallel_groups_diamond(self) -> None:
        """Diamond: first a, then b+c in parallel, then d."""
        graph = DependencyGraph(
            (
                _sub("a"),
                _sub("b", ("a",)),
                _sub("c", ("a",)),
                _sub("d", ("b", "c")),
            )
        )
        groups = graph.parallel_groups()
        assert len(groups) == 3
        assert groups[0] == ("a",)
        assert set(groups[1]) == {"b", "c"}
        assert groups[2] == ("d",)

    @pytest.mark.unit
    def test_get_dependents(self) -> None:
        """get_dependents returns direct dependents."""
        graph = DependencyGraph(
            (
                _sub("a"),
                _sub("b", ("a",)),
                _sub("c", ("a",)),
            )
        )
        assert set(graph.get_dependents("a")) == {"b", "c"}
        assert graph.get_dependents("b") == ()

    @pytest.mark.unit
    def test_get_dependencies(self) -> None:
        """get_dependencies returns direct dependencies."""
        graph = DependencyGraph(
            (
                _sub("a"),
                _sub("b", ("a",)),
                _sub("c", ("a", "b")),
            )
        )
        assert graph.get_dependencies("c") == ("a", "b")
        assert graph.get_dependencies("a") == ()

    @pytest.mark.unit
    def test_get_dependents_unknown_id(self) -> None:
        """get_dependents returns empty for unknown subtask ID."""
        graph = DependencyGraph((_sub("a"),))
        assert graph.get_dependents("nonexistent") == ()

    @pytest.mark.unit
    def test_get_dependencies_unknown_id(self) -> None:
        """get_dependencies returns empty for unknown subtask ID."""
        graph = DependencyGraph((_sub("a"),))
        assert graph.get_dependencies("nonexistent") == ()

    @pytest.mark.unit
    def test_parallel_groups_cycle_raises(self) -> None:
        """parallel_groups raises DecompositionCycleError on cycle."""
        graph = DependencyGraph(
            (
                _sub("a", ("c",)),
                _sub("b", ("a",)),
                _sub("c", ("b",)),
            )
        )
        with pytest.raises(DecompositionCycleError, match="cycle"):
            graph.parallel_groups()

    @pytest.mark.unit
    def test_topological_sort_missing_reference_raises(self) -> None:
        """topological_sort raises DecompositionError for missing references."""
        graph = DependencyGraph((_sub("a", ("missing",)),))
        with pytest.raises(DecompositionError, match="unknown dependency"):
            graph.topological_sort()

    @pytest.mark.unit
    def test_parallel_groups_missing_reference_raises(self) -> None:
        """parallel_groups raises DecompositionError for missing references."""
        graph = DependencyGraph((_sub("a", ("missing",)),))
        with pytest.raises(DecompositionError, match="unknown dependency"):
            graph.parallel_groups()

    @pytest.mark.unit
    def test_single_node(self) -> None:
        """Single node graph validates and sorts correctly."""
        graph = DependencyGraph((_sub("only"),))
        graph.validate()
        assert graph.topological_sort() == ("only",)
        assert graph.parallel_groups() == (("only",),)
