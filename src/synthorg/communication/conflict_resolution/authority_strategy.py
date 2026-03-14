"""Authority + dissent log conflict resolution strategy (see Communication design page).

Strategy 1: The agent with higher seniority wins.  For equal seniority,
hierarchy position decides — using the lowest common manager for
cross-department agents as the tiebreaker.

For N-party conflicts, positions are compared pairwise, accumulating
the winner across all participants.
"""

from datetime import UTC, datetime
from uuid import uuid4

from synthorg.communication.conflict_resolution._helpers import find_losers
from synthorg.communication.conflict_resolution.models import (
    Conflict,
    ConflictPosition,
    ConflictResolution,
    ConflictResolutionOutcome,
    DissentRecord,
)
from synthorg.communication.delegation.hierarchy import (  # noqa: TC001
    HierarchyResolver,
)
from synthorg.communication.enums import ConflictResolutionStrategy
from synthorg.communication.errors import ConflictHierarchyError
from synthorg.core.enums import compare_seniority
from synthorg.observability import get_logger
from synthorg.observability.events.conflict import (
    CONFLICT_AUTHORITY_DECIDED,
    CONFLICT_CROSS_DEPARTMENT,
    CONFLICT_HIERARCHY_ERROR,
    CONFLICT_LCM_LOOKUP,
)

logger = get_logger(__name__)


class AuthorityResolver:
    """Resolve conflicts by seniority and hierarchy position.

    The agent with higher seniority wins.  For equal seniority,
    hierarchy proximity is used as a tiebreaker — the agent closer
    to the hierarchy root (same department) or the lowest common
    manager (cross-department) wins.

    Args:
        hierarchy: Resolved organizational hierarchy.
    """

    __slots__ = ("_hierarchy",)

    def __init__(self, *, hierarchy: HierarchyResolver) -> None:
        self._hierarchy = hierarchy

    async def resolve(self, conflict: Conflict) -> ConflictResolution:
        """Resolve by authority — highest seniority wins.

        Args:
            conflict: The conflict to resolve.

        Returns:
            Resolution with ``RESOLVED_BY_AUTHORITY`` outcome.

        Raises:
            ConflictHierarchyError: If cross-department agents share
                no common manager.
        """
        if conflict.is_cross_department:
            logger.info(
                CONFLICT_CROSS_DEPARTMENT,
                conflict_id=conflict.id,
            )

        winner = self._pick_winner(conflict)
        non_winners = [p for p in conflict.positions if p.agent_id != winner.agent_id]

        logger.info(
            CONFLICT_AUTHORITY_DECIDED,
            conflict_id=conflict.id,
            winner=winner.agent_id,
            losers=[p.agent_id for p in non_winners],
        )

        losers_desc = ", ".join(f"{p.agent_id} ({p.agent_level})" for p in non_winners)
        return ConflictResolution(
            conflict_id=conflict.id,
            outcome=ConflictResolutionOutcome.RESOLVED_BY_AUTHORITY,
            winning_agent_id=winner.agent_id,
            winning_position=winner.position,
            decided_by=winner.agent_id,
            reasoning=(
                f"Authority decision: {winner.agent_id} "
                f"({winner.agent_level}) outranks {losers_desc}"
            ),
            resolved_at=datetime.now(UTC),
        )

    def build_dissent_records(
        self,
        conflict: Conflict,
        resolution: ConflictResolution,
    ) -> tuple[DissentRecord, ...]:
        """Build dissent records for all overruled positions.

        Args:
            conflict: The original conflict.
            resolution: The resolution decision.

        Returns:
            One dissent record per overruled agent.
        """
        losers = find_losers(conflict, resolution)
        return tuple(
            DissentRecord(
                id=f"dissent-{uuid4().hex[:12]}",
                conflict=conflict,
                resolution=resolution,
                dissenting_agent_id=loser.agent_id,
                dissenting_position=loser.position,
                strategy_used=ConflictResolutionStrategy.AUTHORITY,
                timestamp=datetime.now(UTC),
            )
            for loser in losers
        )

    def _pick_winner(
        self,
        conflict: Conflict,
    ) -> ConflictPosition:
        """Determine the winning position from all conflict participants.

        Iterates all positions, comparing seniority pairwise.  Ties
        are broken by hierarchy proximity via ``_resolve_by_hierarchy``.

        Args:
            conflict: The conflict with agent positions.

        Returns:
            The winning position.

        Raises:
            ConflictHierarchyError: If no common manager exists for
                cross-department agents with equal seniority.
        """
        best = conflict.positions[0]
        for pos in conflict.positions[1:]:
            cmp = compare_seniority(pos.agent_level, best.agent_level)
            if cmp > 0:
                best = pos
            elif cmp == 0:
                # Pass best as pos_a so equal depth favors incumbent
                winner, _ = self._resolve_by_hierarchy(
                    conflict,
                    best,
                    pos,
                )
                best = winner
        return best

    def _resolve_by_hierarchy(
        self,
        conflict: Conflict,
        pos_a: ConflictPosition,
        pos_b: ConflictPosition,
    ) -> tuple[ConflictPosition, ConflictPosition]:
        """Break seniority tie using hierarchy position.

        Args:
            conflict: The conflict being resolved.
            pos_a: First position.
            pos_b: Second position.

        Returns:
            Tuple of ``(winner, loser)``.

        Raises:
            ConflictHierarchyError: If no common manager exists.
        """
        lcm = self._hierarchy.get_lowest_common_manager(
            pos_a.agent_id,
            pos_b.agent_id,
        )
        logger.debug(
            CONFLICT_LCM_LOOKUP,
            conflict_id=conflict.id,
            agent_a=pos_a.agent_id,
            agent_b=pos_b.agent_id,
            lcm=lcm,
        )

        if lcm is None:
            msg = f"No common manager for {pos_a.agent_id!r} and {pos_b.agent_id!r}"
            logger.warning(
                CONFLICT_HIERARCHY_ERROR,
                conflict_id=conflict.id,
                agent_a=pos_a.agent_id,
                agent_b=pos_b.agent_id,
                error=msg,
            )
            raise ConflictHierarchyError(
                msg,
                context={
                    "conflict_id": conflict.id,
                    "agent_a": pos_a.agent_id,
                    "agent_b": pos_b.agent_id,
                },
            )

        # Agent closer to LCM (fewer ancestors between) wins.
        depth_a = self._resolve_depth(conflict, lcm, pos_a)
        depth_b = self._resolve_depth(conflict, lcm, pos_b)

        if depth_a <= depth_b:
            return pos_a, pos_b
        return pos_b, pos_a

    def _resolve_depth(
        self,
        conflict: Conflict,
        lcm: str,
        pos: ConflictPosition,
    ) -> int:
        """Resolve hierarchy depth from LCM to an agent.

        ``get_delegation_depth`` returns ``None`` when the agent IS the
        LCM (it only measures downward distance).  This helper treats
        that case as depth 0 and raises for truly unreachable agents.

        Args:
            conflict: The conflict being resolved.
            lcm: Lowest common manager ID.
            pos: The position whose depth to resolve.

        Returns:
            Non-negative depth from LCM to the agent.

        Raises:
            ConflictHierarchyError: If the agent is unreachable.
        """
        depth = self._hierarchy.get_delegation_depth(lcm, pos.agent_id)
        if depth is not None:
            return depth
        if pos.agent_id == lcm:
            return 0
        msg = f"Agent {pos.agent_id!r} unreachable from LCM {lcm!r}"
        logger.warning(
            CONFLICT_HIERARCHY_ERROR,
            conflict_id=conflict.id,
            agent=pos.agent_id,
            lcm=lcm,
            error=msg,
        )
        raise ConflictHierarchyError(
            msg,
            context={
                "conflict_id": conflict.id,
                "agent": pos.agent_id,
                "lcm": lcm,
            },
        )
