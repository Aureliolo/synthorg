"""Dependency graph utilities for subtask DAG analysis.

Pure graph logic operating on ``SubtaskDefinition`` tuples.
Returns immutable tuples for all results.
"""

from typing import TYPE_CHECKING

from ai_company.engine.errors import DecompositionCycleError
from ai_company.observability import get_logger
from ai_company.observability.events.decomposition import (
    DECOMPOSITION_GRAPH_CYCLE,
    DECOMPOSITION_GRAPH_VALIDATED,
)

if TYPE_CHECKING:
    from ai_company.engine.decomposition.models import SubtaskDefinition

logger = get_logger(__name__)


class DependencyGraph:
    """Dependency graph built from subtask definitions.

    Provides validation, topological sorting, and parallel group
    computation for subtask execution ordering.

    Attributes:
        subtasks: The subtask definitions this graph was built from.
    """

    __slots__ = ("_adjacency", "_reverse_adjacency", "_subtask_ids", "subtasks")

    def __init__(self, subtasks: tuple[SubtaskDefinition, ...]) -> None:
        self.subtasks = subtasks
        self._subtask_ids = tuple(s.id for s in subtasks)

        # Forward adjacency: node -> nodes that depend on it
        self._adjacency: dict[str, list[str]] = {sid: [] for sid in self._subtask_ids}
        # Reverse adjacency: node -> its dependencies
        self._reverse_adjacency: dict[str, tuple[str, ...]] = {}

        for subtask in subtasks:
            self._reverse_adjacency[subtask.id] = subtask.dependencies
            for dep in subtask.dependencies:
                if dep in self._adjacency:
                    self._adjacency[dep].append(subtask.id)

    def validate(self) -> None:
        """Validate the dependency graph.

        Checks for missing references and cycles. Raises
        ``DecompositionCycleError`` if a cycle is detected.
        """
        id_set = set(self._subtask_ids)

        # Check for missing references
        for subtask in self.subtasks:
            for dep in subtask.dependencies:
                if dep not in id_set:
                    msg = (
                        f"Subtask {subtask.id!r} references unknown dependency {dep!r}"
                    )
                    raise DecompositionCycleError(msg)

        # Cycle detection via DFS
        visited: set[str] = set()
        in_stack: set[str] = set()

        def dfs(node: str) -> None:
            visited.add(node)
            in_stack.add(node)
            for dep in self._reverse_adjacency.get(node, ()):
                if dep in in_stack:
                    logger.warning(
                        DECOMPOSITION_GRAPH_CYCLE,
                        node=node,
                        dependency=dep,
                    )
                    msg = f"Dependency cycle detected: {node!r} -> {dep!r}"
                    raise DecompositionCycleError(msg)
                if dep not in visited:
                    dfs(dep)
            in_stack.discard(node)

        for sid in self._subtask_ids:
            if sid not in visited:
                dfs(sid)

        logger.debug(
            DECOMPOSITION_GRAPH_VALIDATED,
            subtask_count=len(self._subtask_ids),
        )

    def topological_sort(self) -> tuple[str, ...]:
        """Return subtask IDs in topological execution order.

        Dependencies come before dependents. Uses Kahn's algorithm.

        Returns:
            Tuple of subtask IDs in execution order.

        Raises:
            DecompositionCycleError: If a cycle prevents sorting.
        """
        in_degree = dict.fromkeys(self._subtask_ids, 0)
        for subtask in self.subtasks:
            for _dep in subtask.dependencies:
                in_degree[subtask.id] += 1

        queue = [sid for sid in self._subtask_ids if in_degree[sid] == 0]
        result: list[str] = []

        while queue:
            # Sort for deterministic output
            queue.sort()
            node = queue.pop(0)
            result.append(node)
            for dependent in self._adjacency.get(node, []):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        if len(result) != len(self._subtask_ids):
            msg = "Dependency cycle detected during topological sort"
            raise DecompositionCycleError(msg)

        return tuple(result)

    def parallel_groups(self) -> tuple[tuple[str, ...], ...]:
        """Compute groups of subtasks that can execute in parallel.

        Each group contains subtasks whose dependencies are all
        satisfied by earlier groups. Groups execute in sequence;
        subtasks within a group can run concurrently.

        Returns:
            Tuple of groups, each group a tuple of subtask IDs.
        """
        in_degree = dict.fromkeys(self._subtask_ids, 0)
        for subtask in self.subtasks:
            for _dep in subtask.dependencies:
                in_degree[subtask.id] += 1

        remaining = set(self._subtask_ids)
        groups: list[tuple[str, ...]] = []

        while remaining:
            # Find all nodes with in_degree == 0 among remaining
            ready = sorted(sid for sid in remaining if in_degree[sid] == 0)
            if not ready:
                msg = "Dependency cycle detected during parallel grouping"
                raise DecompositionCycleError(msg)

            groups.append(tuple(ready))

            # Remove ready nodes and update in-degrees
            for node in ready:
                remaining.discard(node)
                for dependent in self._adjacency.get(node, []):
                    in_degree[dependent] -= 1

        return tuple(groups)

    def get_dependents(self, subtask_id: str) -> tuple[str, ...]:
        """Get IDs of subtasks that depend on the given subtask.

        Args:
            subtask_id: The subtask to find dependents for.

        Returns:
            Tuple of dependent subtask IDs.
        """
        return tuple(sorted(self._adjacency.get(subtask_id, [])))

    def get_dependencies(self, subtask_id: str) -> tuple[str, ...]:
        """Get IDs of subtasks that the given subtask depends on.

        Args:
            subtask_id: The subtask to find dependencies for.

        Returns:
            Tuple of dependency subtask IDs.
        """
        return tuple(sorted(self._reverse_adjacency.get(subtask_id, ())))
