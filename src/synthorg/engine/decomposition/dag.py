"""Dependency graph utilities for subtask DAG analysis.

Pure graph logic operating on sequences of ``SubtaskDefinition`` objects.
Returns immutable tuples for all results.
"""

import heapq
from types import MappingProxyType
from typing import TYPE_CHECKING

from synthorg.engine.errors import DecompositionCycleError, DecompositionError
from synthorg.observability import get_logger
from synthorg.observability.events.decomposition import (
    DECOMPOSITION_GRAPH_BUILT,
    DECOMPOSITION_GRAPH_CYCLE,
    DECOMPOSITION_GRAPH_VALIDATED,
    DECOMPOSITION_REFERENCE_ERROR,
)

if TYPE_CHECKING:
    from synthorg.engine.decomposition.models import SubtaskDefinition

logger = get_logger(__name__)


class DependencyGraph:
    """Dependency graph built from subtask definitions.

    Provides validation, topological sorting, and parallel group
    computation for subtask execution ordering.

    Internal adjacency data is frozen after construction via
    ``MappingProxyType`` wrapping and tuple values.

    Attributes:
        subtasks: The subtask definitions this graph was built from.
    """

    __slots__ = ("_adjacency", "_reverse_adjacency", "_subtask_ids", "subtasks")

    def __init__(self, subtasks: tuple[SubtaskDefinition, ...]) -> None:
        self.subtasks = subtasks
        self._subtask_ids = tuple(s.id for s in subtasks)

        # Build mutable adjacency during construction
        forward: dict[str, list[str]] = {sid: [] for sid in self._subtask_ids}
        reverse: dict[str, tuple[str, ...]] = {}

        for subtask in subtasks:
            reverse[subtask.id] = subtask.dependencies
            for dep in subtask.dependencies:
                if dep in forward:
                    forward[dep].append(subtask.id)

        # Freeze: convert lists to tuples and wrap with MappingProxyType
        self._adjacency: MappingProxyType[str, tuple[str, ...]] = MappingProxyType(
            {k: tuple(v) for k, v in forward.items()}
        )
        self._reverse_adjacency: MappingProxyType[str, tuple[str, ...]] = (
            MappingProxyType(reverse)
        )

        edge_count = sum(len(v) for v in self._adjacency.values())
        logger.debug(
            DECOMPOSITION_GRAPH_BUILT,
            subtask_count=len(subtasks),
            edge_count=edge_count,
        )

    def validate(self) -> None:
        """Validate the dependency graph.

        Checks for missing references and cycles.

        Raises:
            DecompositionError: If a dependency references an unknown
                subtask ID.
            DecompositionCycleError: If a cycle is detected.
        """
        self._check_missing_references()
        self._check_cycles()

        logger.debug(
            DECOMPOSITION_GRAPH_VALIDATED,
            subtask_count=len(self._subtask_ids),
        )

    def _check_missing_references(self) -> None:
        """Check for dependencies referencing unknown subtask IDs."""
        id_set = set(self._subtask_ids)
        for subtask in self.subtasks:
            for dep in subtask.dependencies:
                if dep not in id_set:
                    msg = (
                        f"Subtask {subtask.id!r} references unknown dependency {dep!r}"
                    )
                    logger.warning(
                        DECOMPOSITION_REFERENCE_ERROR,
                        subtask_id=subtask.id,
                        unknown_dep=dep,
                        error=msg,
                    )
                    raise DecompositionError(msg)

    def _check_cycles(self) -> None:
        """Iterative DFS cycle detection to avoid stack overflow on deep chains."""
        visited: set[str] = set()
        in_stack: set[str] = set()

        for start in self._subtask_ids:
            if start in visited:
                continue

            stack: list[tuple[str, int]] = [(start, 0)]
            while stack:
                node, idx = stack[-1]

                if idx == 0:
                    visited.add(node)
                    in_stack.add(node)

                deps = self._reverse_adjacency.get(node, ())
                if idx < len(deps):
                    stack[-1] = (node, idx + 1)
                    dep = deps[idx]
                    if dep in in_stack:
                        logger.warning(
                            DECOMPOSITION_GRAPH_CYCLE,
                            node=node,
                            dependency=dep,
                        )
                        msg = f"Dependency cycle detected: {node!r} -> {dep!r}"
                        raise DecompositionCycleError(msg)
                    if dep not in visited:
                        stack.append((dep, 0))
                else:
                    in_stack.discard(node)
                    stack.pop()

    def topological_sort(self) -> tuple[str, ...]:
        """Return subtask IDs in topological execution order.

        Dependencies come before dependents. Uses Kahn's algorithm
        with a min-heap for deterministic ordering.

        Returns:
            Tuple of subtask IDs in execution order.

        Raises:
            DecompositionError: If a dependency references an unknown
                subtask ID.
            DecompositionCycleError: If a cycle prevents sorting.
        """
        self._check_missing_references()

        in_degree: dict[str, int] = dict.fromkeys(self._subtask_ids, 0)
        for subtask in self.subtasks:
            for _dep in subtask.dependencies:
                in_degree[subtask.id] += 1

        heap = [sid for sid in self._subtask_ids if in_degree[sid] == 0]
        heapq.heapify(heap)
        result: list[str] = []

        while heap:
            node = heapq.heappop(heap)
            result.append(node)
            for dependent in self._adjacency.get(node, ()):
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    heapq.heappush(heap, dependent)

        if len(result) != len(self._subtask_ids):
            logger.warning(
                DECOMPOSITION_GRAPH_CYCLE,
                context="topological_sort",
                sorted_count=len(result),
                total_count=len(self._subtask_ids),
            )
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

        Raises:
            DecompositionError: If a dependency references an unknown
                subtask ID.
            DecompositionCycleError: If a cycle prevents grouping.
        """
        self._check_missing_references()

        in_degree: dict[str, int] = dict.fromkeys(self._subtask_ids, 0)
        for subtask in self.subtasks:
            for _dep in subtask.dependencies:
                in_degree[subtask.id] += 1

        remaining = set(self._subtask_ids)
        groups: list[tuple[str, ...]] = []

        while remaining:
            ready = sorted(sid for sid in remaining if in_degree[sid] == 0)
            if not ready:
                logger.warning(
                    DECOMPOSITION_GRAPH_CYCLE,
                    context="parallel_groups",
                    remaining_count=len(remaining),
                )
                msg = "Dependency cycle detected during parallel grouping"
                raise DecompositionCycleError(msg)

            groups.append(tuple(ready))

            for node in ready:
                remaining.discard(node)
                for dependent in self._adjacency.get(node, ()):
                    in_degree[dependent] -= 1

        return tuple(groups)

    def get_dependents(self, subtask_id: str) -> tuple[str, ...]:
        """Get IDs of subtasks that depend on the given subtask.

        Args:
            subtask_id: The subtask to find dependents for.

        Returns:
            Tuple of dependent subtask IDs, or empty tuple if
            the subtask ID is not in the graph.
        """
        return tuple(sorted(self._adjacency.get(subtask_id, ())))

    def get_dependencies(self, subtask_id: str) -> tuple[str, ...]:
        """Get IDs of subtasks that the given subtask depends on.

        Args:
            subtask_id: The subtask to find dependencies for.

        Returns:
            Tuple of dependency subtask IDs, or empty tuple if
            the subtask ID is not in the graph.
        """
        return tuple(sorted(self._reverse_adjacency.get(subtask_id, ())))
