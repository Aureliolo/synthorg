"""Shared helpers for conflict resolution strategies."""

from synthorg.communication.conflict_resolution.models import (  # noqa: TC001
    Conflict,
    ConflictPosition,
    ConflictResolution,
)
from synthorg.communication.delegation.hierarchy import (  # noqa: TC001
    HierarchyResolver,
)
from synthorg.communication.errors import (
    ConflictStrategyError,
)
from synthorg.core.enums import compare_seniority
from synthorg.observability import get_logger
from synthorg.observability.events.conflict import CONFLICT_STRATEGY_ERROR

logger = get_logger(__name__)


def find_losers(
    conflict: Conflict,
    resolution: ConflictResolution,
) -> tuple[ConflictPosition, ...]:
    """Find all non-winning positions in a conflict.

    Returns every position whose agent was not the winner.  For
    2-party conflicts this is a single position; for N-party
    conflicts (3+ agents) this may return multiple.

    Args:
        conflict: The original conflict.
        resolution: The resolution decision.

    Returns:
        All losing agents' positions.

    Raises:
        ConflictStrategyError: If the winning agent ID is not found
            in the conflict positions, or if no losing position is
            found (data integrity violation).
    """
    winner_id = resolution.winning_agent_id
    position_ids = {pos.agent_id for pos in conflict.positions}
    if winner_id not in position_ids:
        msg = (
            f"Winning agent {winner_id!r} not found in "
            f"conflict positions {sorted(position_ids)!r}"
        )
        logger.error(
            CONFLICT_STRATEGY_ERROR,
            conflict_id=conflict.id,
            winning_agent_id=winner_id,
            position_agent_ids=sorted(position_ids),
            error=msg,
        )
        raise ConflictStrategyError(
            msg,
            context={
                "conflict_id": conflict.id,
                "winning_agent_id": winner_id,
                "position_agent_ids": sorted(position_ids),
            },
        )

    losers = tuple(pos for pos in conflict.positions if pos.agent_id != winner_id)
    if not losers:
        msg = f"No losing position found for winner {winner_id!r}"
        logger.warning(
            CONFLICT_STRATEGY_ERROR,
            conflict_id=conflict.id,
            winning_agent_id=winner_id,
            error=msg,
        )
        raise ConflictStrategyError(
            msg,
            context={"conflict_id": conflict.id},
        )
    return losers


def find_position(
    conflict: Conflict,
    agent_id: str,
) -> ConflictPosition | None:
    """Find a position by agent ID, or None if not found.

    Args:
        conflict: The conflict.
        agent_id: Agent to find.

    Returns:
        The matching position, or None.
    """
    for pos in conflict.positions:
        if pos.agent_id == agent_id:
            return pos
    return None


def find_position_or_raise(
    conflict: Conflict,
    agent_id: str,
) -> ConflictPosition:
    """Find a position by agent ID, raising if not found.

    Args:
        conflict: The conflict.
        agent_id: Agent to find.

    Returns:
        The matching position.

    Raises:
        ConflictStrategyError: If agent is not found in positions.
    """
    pos = find_position(conflict, agent_id)
    if pos is not None:
        return pos
    msg = f"Agent {agent_id!r} not found in conflict positions"
    logger.warning(
        CONFLICT_STRATEGY_ERROR,
        conflict_id=conflict.id,
        agent_id=agent_id,
        error=msg,
    )
    raise ConflictStrategyError(
        msg,
        context={
            "conflict_id": conflict.id,
            "agent_id": agent_id,
        },
    )


def pick_highest_seniority(
    conflict: Conflict,
    *,
    hierarchy: HierarchyResolver | None = None,
) -> ConflictPosition:
    """Pick the position with the highest seniority level.

    When two agents share the same seniority level and a
    ``hierarchy`` is provided, the agent closer to the hierarchy
    root (fewer ancestors) wins.  Without a hierarchy, the
    incumbent (first encountered) is kept on ties.

    Args:
        conflict: The conflict with agent positions.
        hierarchy: Optional hierarchy resolver for tiebreaking
            when seniority levels are equal.

    Returns:
        The position with the highest seniority.
    """
    best = conflict.positions[0]
    for pos in conflict.positions[1:]:
        cmp = compare_seniority(pos.agent_level, best.agent_level)
        if cmp > 0:
            best = pos
        elif cmp == 0 and hierarchy is not None:
            best = _hierarchy_tiebreak(best, pos, hierarchy)
    return best


def _hierarchy_tiebreak(
    incumbent: ConflictPosition,
    challenger: ConflictPosition,
    hierarchy: HierarchyResolver,
) -> ConflictPosition:
    """Break a seniority tie using hierarchy depth.

    The agent with fewer ancestors (closer to root) wins.
    If both have the same depth, the incumbent is kept.

    Args:
        incumbent: Current best position.
        challenger: Position challenging the incumbent.
        hierarchy: Hierarchy resolver for depth lookup.

    Returns:
        The winning position.
    """
    depth_inc = len(hierarchy.get_ancestors(incumbent.agent_id))
    depth_chl = len(hierarchy.get_ancestors(challenger.agent_id))
    if depth_chl < depth_inc:
        return challenger
    return incumbent
