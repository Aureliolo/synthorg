"""Shared graph utilities for workflow definitions.

Provides topological sorting and adjacency map construction
used by both the YAML export and workflow execution subsystems.
"""

from collections import defaultdict, deque

from synthorg.engine.workflow.definition import WorkflowDefinition
from synthorg.engine.workflow.enums import WorkflowEdgeType


def topological_sort(
    node_ids: list[str],
    adjacency: dict[str, list[str]],
) -> list[str]:
    """Kahn's algorithm for topological ordering.

    Args:
        node_ids: All node IDs in the graph.
        adjacency: Forward adjacency list (source -> targets).

    Returns:
        Topologically sorted node IDs.

    Raises:
        ValueError: If the graph contains a cycle.
    """
    in_degree: dict[str, int] = dict.fromkeys(node_ids, 0)
    for targets in adjacency.values():
        for target in targets:
            if target in in_degree:
                in_degree[target] += 1

    queue: deque[str] = deque(nid for nid, deg in in_degree.items() if deg == 0)
    result: list[str] = []

    while queue:
        current = queue.popleft()
        result.append(current)
        for neighbor in adjacency.get(current, []):
            if neighbor in in_degree:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

    if len(result) != len(node_ids):
        msg = "Cannot topologically sort: graph contains a cycle"
        raise ValueError(msg)

    return result


def build_adjacency_maps(
    definition: WorkflowDefinition,
) -> tuple[
    dict[str, list[str]],
    dict[str, list[str]],
    dict[str, list[tuple[str, WorkflowEdgeType]]],
]:
    """Build forward, reverse, and typed-outgoing adjacency maps.

    Args:
        definition: A workflow definition.

    Returns:
        A 3-tuple of:
        - ``adjacency``: forward adjacency (source -> [targets])
        - ``reverse_adj``: reverse adjacency (target -> [sources])
        - ``outgoing``: typed outgoing edges
          (source -> [(target, edge_type)])
    """
    adjacency: dict[str, list[str]] = defaultdict(list)
    reverse_adj: dict[str, list[str]] = defaultdict(list)
    outgoing: dict[str, list[tuple[str, WorkflowEdgeType]]] = defaultdict(list)
    for edge in definition.edges:
        adjacency[edge.source_node_id].append(edge.target_node_id)
        reverse_adj[edge.target_node_id].append(edge.source_node_id)
        outgoing[edge.source_node_id].append(
            (edge.target_node_id, edge.type),
        )
    return dict(adjacency), dict(reverse_adj), dict(outgoing)
