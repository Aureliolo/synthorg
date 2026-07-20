# module-kind: code
"""Longest dependency chain through a plan's item DAG.

The critical path is the chain that sets the initiative's delivery date: every
item on it must finish in sequence, so shortening any other chain does not
bring the plan in sooner.

Computed here rather than in the browser so it is reachable by any API client
rather than only the dashboard, and so the graph logic lives beside the plan
model that already owns dependency validation.
"""

from collections.abc import Mapping
from typing import Final

#: Depth-first visit marks. Cycle detection needs to distinguish "on the
#: current path" from "fully resolved", so a plain visited set is not enough.
_UNVISITED: Final[int] = 0
_IN_PROGRESS: Final[int] = 1
_DONE: Final[int] = 2


def longest_dependency_chain[K: str](
    dependencies: Mapping[K, tuple[K, ...]],
) -> tuple[K, ...]:
    """Return the longest chain through *dependencies*, in dependency order.

    Args:
        dependencies: Map of node to the nodes it depends on. A dependency on
            an unknown node is ignored, so a plan whose decision items were
            stripped at dispatch still resolves.

    Returns:
        The nodes of a longest chain, ordered from root to leaf, or ``()``
        when the graph is empty or contains a cycle. Ties are broken on sorted
        node order so repeated calls return the same chain.
    """
    nodes = sorted(dependencies)
    state: dict[K, int] = dict.fromkeys(nodes, _UNVISITED)
    # Memoised longest chain ending at each node.
    best: dict[K, tuple[K, ...]] = {}

    def _chain_ending_at(node: K) -> tuple[K, ...] | None:
        """Return the longest chain ending at *node*, or ``None`` on a cycle."""
        if state[node] == _IN_PROGRESS:
            return None
        if state[node] == _DONE:
            return best[node]
        state[node] = _IN_PROGRESS
        longest: tuple[K, ...] = ()
        for parent in sorted(dependencies[node]):
            if parent not in state:
                # Dependency on a node outside the graph (e.g. a decision item
                # stripped before dispatch); it constrains nothing here.
                continue
            resolved = _chain_ending_at(parent)
            if resolved is None:
                return None
            if _outranks(resolved, longest):
                longest = resolved
        state[node] = _DONE
        best[node] = (*longest, node)
        return best[node]

    overall: tuple[K, ...] = ()
    for node in nodes:
        chain = _chain_ending_at(node)
        if chain is None:
            return ()
        if _outranks(chain, overall):
            overall = chain
    return overall


def _outranks[K: str](candidate: tuple[K, ...], incumbent: tuple[K, ...]) -> bool:
    """Return whether *candidate* should replace *incumbent*.

    Longer wins; equal lengths break on sorted order so the result is stable
    across calls rather than depending on iteration order.
    """
    if len(candidate) != len(incumbent):
        return len(candidate) > len(incumbent)
    return bool(candidate) and candidate < incumbent
