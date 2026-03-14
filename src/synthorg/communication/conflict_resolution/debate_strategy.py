"""Structured debate + judge conflict resolution strategy.

See the Communication design page for background.

Strategy 2: A judge evaluates both positions and picks a winner.
If a ``JudgeEvaluator`` is provided, it uses LLM-based judging.
Otherwise, falls back to authority-based resolution (highest
seniority among positions wins).
"""

from datetime import UTC, datetime
from uuid import uuid4

from synthorg.communication.conflict_resolution._helpers import (
    find_losers,
    find_position_or_raise,
    pick_highest_seniority,
)
from synthorg.communication.conflict_resolution.config import (  # noqa: TC001
    DebateConfig,
)
from synthorg.communication.conflict_resolution.models import (
    Conflict,
    ConflictResolution,
    ConflictResolutionOutcome,
    DissentRecord,
)
from synthorg.communication.conflict_resolution.protocol import (
    JudgeDecision,
    JudgeEvaluator,
)
from synthorg.communication.delegation.hierarchy import (  # noqa: TC001
    HierarchyResolver,
)
from synthorg.communication.enums import ConflictResolutionStrategy
from synthorg.communication.errors import ConflictHierarchyError
from synthorg.observability import get_logger
from synthorg.observability.events.conflict import (
    CONFLICT_AUTHORITY_FALLBACK,
    CONFLICT_DEBATE_JUDGE_DECIDED,
    CONFLICT_DEBATE_STARTED,
    CONFLICT_HIERARCHY_ERROR,
    CONFLICT_LCM_LOOKUP,
    CONFLICT_STRATEGY_ERROR,
)

logger = get_logger(__name__)


class DebateResolver:
    """Resolve conflicts via structured debate with a judge.

    When a ``JudgeEvaluator`` is provided, the judge evaluates
    both positions using LLM reasoning.  When absent, falls back
    to authority-based resolution (highest seniority wins).

    Args:
        hierarchy: Resolved organizational hierarchy.
        config: Debate strategy configuration.
        judge_evaluator: Optional LLM-based judge (fallback: authority).
    """

    __slots__ = ("_config", "_hierarchy", "_judge_evaluator")

    def __init__(
        self,
        *,
        hierarchy: HierarchyResolver,
        config: DebateConfig,
        judge_evaluator: JudgeEvaluator | None = None,
    ) -> None:
        self._hierarchy = hierarchy
        self._config = config
        self._judge_evaluator = judge_evaluator

    async def resolve(self, conflict: Conflict) -> ConflictResolution:
        """Resolve via debate — judge picks a winner.

        Args:
            conflict: The conflict to resolve.

        Returns:
            Resolution with ``RESOLVED_BY_DEBATE`` outcome.

        Raises:
            ConflictStrategyError: If the judge returns a winning
                agent ID not found in the conflict positions.
            ConflictHierarchyError: If LCM lookup fails when needed.
        """
        judge_id = self._determine_judge(conflict)

        logger.info(
            CONFLICT_DEBATE_STARTED,
            conflict_id=conflict.id,
            judge=judge_id,
        )

        if self._judge_evaluator is not None:
            try:
                winning_agent_id, reasoning = await self._judge_evaluator.evaluate(
                    conflict,
                    judge_id,
                )
            except Exception:
                logger.exception(
                    CONFLICT_STRATEGY_ERROR,
                    conflict_id=conflict.id,
                    strategy="debate",
                    operation="judge_evaluate",
                    judge=judge_id,
                )
                raise
        else:
            logger.warning(
                CONFLICT_AUTHORITY_FALLBACK,
                conflict_id=conflict.id,
                strategy="debate",
                reason="no_judge_evaluator",
            )
            winning_agent_id, reasoning = self._authority_fallback(conflict)

        winning_pos = find_position_or_raise(conflict, winning_agent_id)

        logger.info(
            CONFLICT_DEBATE_JUDGE_DECIDED,
            conflict_id=conflict.id,
            judge=judge_id,
            winner=winning_agent_id,
        )

        return ConflictResolution(
            conflict_id=conflict.id,
            outcome=ConflictResolutionOutcome.RESOLVED_BY_DEBATE,
            winning_agent_id=winning_agent_id,
            winning_position=winning_pos.position,
            decided_by=judge_id,
            reasoning=reasoning,
            resolved_at=datetime.now(UTC),
        )

    def build_dissent_records(
        self,
        conflict: Conflict,
        resolution: ConflictResolution,
    ) -> tuple[DissentRecord, ...]:
        """Build dissent records for all overruled debaters.

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
                strategy_used=ConflictResolutionStrategy.DEBATE,
                timestamp=datetime.now(UTC),
                metadata=(("judge", resolution.decided_by),),
            )
            for loser in losers
        )

    def _determine_judge(self, conflict: Conflict) -> str:
        """Determine the judge agent for this conflict.

        For N-party conflicts with ``"shared_manager"``, finds the
        lowest common manager of all participants iteratively.

        Args:
            conflict: The conflict being judged.

        Returns:
            Agent name to act as judge.

        Raises:
            ConflictHierarchyError: If ``"shared_manager"`` is
                configured but no LCM exists.
        """
        if self._config.judge == "shared_manager":
            lcm: str | None = self._hierarchy.get_lowest_common_manager(
                conflict.positions[0].agent_id,
                conflict.positions[1].agent_id,
            )
            for pos in conflict.positions[2:]:
                if lcm is None:
                    break
                lcm = self._hierarchy.get_lowest_common_manager(
                    lcm,
                    pos.agent_id,
                )
            logger.debug(
                CONFLICT_LCM_LOOKUP,
                conflict_id=conflict.id,
                agents=[p.agent_id for p in conflict.positions],
                lcm=lcm,
            )
            if lcm is None:
                msg = (
                    "No shared manager for conflict participants — cannot select judge"
                )
                logger.warning(
                    CONFLICT_HIERARCHY_ERROR,
                    conflict_id=conflict.id,
                    agents=[p.agent_id for p in conflict.positions],
                    error=msg,
                )
                raise ConflictHierarchyError(
                    msg,
                    context={
                        "conflict_id": conflict.id,
                        "agents": [p.agent_id for p in conflict.positions],
                    },
                )
            return lcm

        if self._config.judge == "ceo":
            # Walk from any position to hierarchy root
            for pos in conflict.positions:
                ancestors = self._hierarchy.get_ancestors(pos.agent_id)
                if ancestors:
                    return ancestors[-1]
            # All positions are roots or not in hierarchy; use first
            agent_id = conflict.positions[0].agent_id
            logger.warning(
                CONFLICT_HIERARCHY_ERROR,
                conflict_id=conflict.id,
                agent=agent_id,
                error="No ancestors found for any position; using as CEO/judge",
            )
            return agent_id

        # Named agent — not validated against hierarchy at config time;
        # invalid names surface at evaluation time.
        return self._config.judge

    def _authority_fallback(
        self,
        conflict: Conflict,
    ) -> JudgeDecision:
        """Fall back to authority when no judge evaluator is available.

        Uses hierarchy as a tiebreaker when seniority levels are equal.

        Args:
            conflict: The conflict to resolve.

        Returns:
            Decision with winning agent ID and reasoning.
        """
        best = pick_highest_seniority(conflict, hierarchy=self._hierarchy)
        return JudgeDecision(
            winning_agent_id=best.agent_id,
            reasoning=(
                f"Debate fallback: authority-based judging — "
                f"{best.agent_id} ({best.agent_level}) has highest "
                f"seniority"
            ),
        )
