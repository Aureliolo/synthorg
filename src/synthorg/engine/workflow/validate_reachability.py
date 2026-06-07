"""Reachability and cycle detection for workflow graphs."""

from collections import deque
from typing import TYPE_CHECKING

from synthorg.engine.workflow.enums import WorkflowNodeType
from synthorg.engine.workflow.validation_types import (
    ValidationErrorCode,
    WorkflowValidationError,
)

if TYPE_CHECKING:
    from synthorg.engine.workflow.definition import WorkflowDefinition


def reachable_from(
    start_id: str,
    adjacency: dict[str, list[str]],
) -> frozenset[str]:
    """BFS to find all nodes reachable from *start_id*.

    Returns:
        Frozenset of node ids reachable from ``start_id`` via the
        forward adjacency map (inclusive of ``start_id``).
    """
    visited: set[str] = set()
    queue: deque[str] = deque([start_id])
    while queue:
        current = queue.popleft()
        if current in visited:
            continue
        visited.add(current)
        queue.extend(n for n in adjacency.get(current, []) if n not in visited)
    return frozenset(visited)


def has_cycle(
    node_ids: frozenset[str],
    adjacency: dict[str, list[str]],
) -> bool:
    """Detect cycles using iterative DFS coloring (white/gray/black).

    Returns:
        ``True`` when the directed graph defined by ``adjacency``
        contains a cycle reachable within ``node_ids``; ``False``
        otherwise.
    """
    white, gray, black = 0, 1, 2
    color: dict[str, int] = dict.fromkeys(node_ids, white)

    for start in node_ids:
        if color[start] != white:
            continue
        stack: list[tuple[str, int]] = [(start, 0)]
        color[start] = gray
        while stack:
            nid, idx = stack[-1]
            neighbors = adjacency.get(nid, [])
            if idx < len(neighbors):
                stack[-1] = (nid, idx + 1)
                neighbor = neighbors[idx]
                if neighbor not in color:
                    continue
                if color[neighbor] == gray:
                    return True
                if color[neighbor] == white:
                    color[neighbor] = gray
                    stack.append((neighbor, 0))
            else:
                stack.pop()
                color[nid] = black

    return False


def check_reachability(
    definition: WorkflowDefinition,
    adjacency: dict[str, list[str]],
) -> list[WorkflowValidationError]:
    """Check all nodes reachable from START and END reachable.

    Returns:
        List of :class:`WorkflowValidationError` records: one per
        non-END node unreachable from START, plus one when END
        itself is unreachable.
    """
    errors: list[WorkflowValidationError] = []
    start = next(n for n in definition.nodes if n.type == WorkflowNodeType.START)
    end = next(n for n in definition.nodes if n.type == WorkflowNodeType.END)
    reachable = reachable_from(start.id, adjacency)

    errors.extend(
        WorkflowValidationError(
            code=ValidationErrorCode.UNREACHABLE_NODE,
            message=f"Node {node.id!r} is not reachable from START",
            node_id=node.id,
        )
        for node in definition.nodes
        if node.id not in reachable and node.type != WorkflowNodeType.END
    )

    if end.id not in reachable:
        errors.append(
            WorkflowValidationError(
                code=ValidationErrorCode.END_NOT_REACHABLE,
                message="END node is not reachable from START",
                node_id=end.id,
            ),
        )
    return errors
