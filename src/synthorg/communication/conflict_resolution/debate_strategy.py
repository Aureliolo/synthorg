"""Structured debate + judge conflict resolution strategy.

See the Communication design page for background.

Strategy 2: A judge evaluates both positions and picks a winner.
If a ``JudgeEvaluator`` is provided, it uses LLM-based judging.
Otherwise, falls back to authority-based resolution (highest
seniority among positions wins).
"""

from datetime import UTC, datetime
from uuid import uuid4

from synthorg.communication.conflict_resolution._evidence import extract_evidence
from synthorg.communication.conflict_resolution._helpers import (
    find_losers,
    find_position,
    find_position_or_raise,
    pick_highest_seniority,
)
from synthorg.communication.conflict_resolution.config import (
    DebateConfig,
)
from synthorg.communication.conflict_resolution.models import (
    Conflict,
    ConflictPosition,
    ConflictResolution,
    ConflictResolutionOutcome,
    DissentRecord,
)
from synthorg.communication.conflict_resolution.protocol import (
    JudgeDecision,
    JudgeEvaluator,
)
from synthorg.communication.delegation.hierarchy import (
    HierarchyResolver,
)
from synthorg.communication.enums import ConflictResolutionStrategy
from synthorg.communication.errors import ConflictHierarchyError
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.conflict import (
    CONFLICT_AMBIGUOUS_RESULT,
    CONFLICT_AUTHORITY_FALLBACK,
    CONFLICT_DEBATE_EVALUATOR_FAILED,
    CONFLICT_DEBATE_JUDGE_DECIDED,
    CONFLICT_DEBATE_STARTED,
    CONFLICT_HIERARCHY_ERROR,
    CONFLICT_LCM_LOOKUP,
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
        """Resolve via debate -- judge picks a winner.

        Args:
            conflict: The conflict to resolve.

        Returns:
            Resolution with the ``RESOLVED_BY_DEBATE`` outcome, or
            ``RESOLVED_BY_AUTHORITY`` when no evaluator is wired, the
            evaluator fails, or its verdict is ambiguous/non-participant.

        Raises:
            ConflictHierarchyError: If LCM lookup fails when needed.
        """
        judge_id = self._determine_judge(conflict)

        logger.info(
            CONFLICT_DEBATE_STARTED,
            conflict_id=str(conflict.id),
            judge=judge_id,
        )

        # Records the resolution path truthfully: a judged debate stays
        # RESOLVED_BY_DEBATE, but any fallback to seniority (no evaluator,
        # evaluator failure, or an ambiguous verdict) must be recorded as
        # RESOLVED_BY_AUTHORITY so the persisted outcome and dissent audit
        # trail do not misattribute the decision to a debate that never ran.
        decision = await self._judge(conflict, judge_id)
        outcome = ConflictResolutionOutcome.RESOLVED_BY_DEBATE
        if decision is None:
            outcome = ConflictResolutionOutcome.RESOLVED_BY_AUTHORITY
            decision = self._authority_fallback_safe(conflict)

        winning_pos = find_position_or_raise(conflict, decision.winning_agent_id)

        logger.info(
            CONFLICT_DEBATE_JUDGE_DECIDED,
            conflict_id=str(conflict.id),
            judge=judge_id,
            winner=decision.winning_agent_id,
        )

        return ConflictResolution(
            conflict_id=str(conflict.id),
            outcome=outcome,
            winning_agent_id=decision.winning_agent_id,
            winning_position=winning_pos.position,
            decided_by=judge_id,
            reasoning=decision.reasoning,
            resolved_at=datetime.now(UTC),
        )

    async def _judge(
        self,
        conflict: Conflict,
        judge_id: str,
    ) -> JudgeDecision | None:
        """Run the LLM judge, or ``None`` to signal an authority fallback.

        Returns ``None`` when no evaluator is wired, the evaluator fails, or
        it returns a non-participant winner (the ambiguity sentinel or a
        hallucinated id): debate has no human-escalation arm, so every such
        case degrades to authority-based judging.

        Returns:
            The judge's decision, or ``None`` to fall back to authority.
        """
        if self._judge_evaluator is None:
            logger.warning(
                CONFLICT_AUTHORITY_FALLBACK,
                conflict_id=str(conflict.id),
                strategy="debate",
                reason="no_judge_evaluator",
            )
            return None
        try:
            decision = await self._judge_evaluator.evaluate(conflict, judge_id)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            # WARNING, not ERROR: this is a recovered fallback to authority
            # (like the no-evaluator and ambiguous-verdict siblings), not a
            # failed resolution.
            logger.warning(
                CONFLICT_DEBATE_EVALUATOR_FAILED,
                conflict_id=str(conflict.id),
                judge=judge_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return None
        if find_position(conflict, decision.winning_agent_id) is None:
            logger.warning(
                CONFLICT_AMBIGUOUS_RESULT,
                conflict_id=str(conflict.id),
                returned_winner=decision.winning_agent_id,
                participants=[p.agent_id for p in conflict.positions],
            )
            return None
        return decision

    def _authority_fallback_safe(self, conflict: Conflict) -> JudgeDecision:
        """Authority fallback that always yields a winner.

        Wraps :meth:`_authority_fallback` with a hierarchy-failure safety
        net so a failed lowest-common-manager lookup still resolves by raw
        seniority rather than raising.

        Returns:
            The authority-based decision.
        """
        try:
            return self._authority_fallback(conflict)
        except ConflictHierarchyError:
            logger.warning(
                CONFLICT_HIERARCHY_ERROR,
                conflict_id=str(conflict.id),
                note="authority fallback hierarchy failed; "
                "using seniority without hierarchy",
            )
            best = pick_highest_seniority(conflict, hierarchy=None)
            return JudgeDecision(
                winning_agent_id=best.agent_id,
                reasoning=_seniority_reasoning(best, no_hierarchy=True),
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
                id=uuid4(),
                conflict=conflict,
                resolution=resolution,
                dissenting_agent_id=loser.agent_id,
                dissenting_position=loser.position,
                strategy_used=ConflictResolutionStrategy.DEBATE,
                timestamp=datetime.now(UTC),
                minority_evidence=extract_evidence(loser.reasoning),
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
            return self._shared_manager_judge(conflict)

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
                conflict_id=str(conflict.id),
                agent=agent_id,
                error="No ancestors found for any position; using as CEO/judge",
            )
            return agent_id

        # Named agent -- not validated against hierarchy at config time;
        # invalid names surface at evaluation time.
        return self._config.judge

    def _shared_manager_judge(self, conflict: Conflict) -> str:
        """Find the lowest common manager of all participants, iteratively.

        Returns:
            The shared-manager agent id to act as judge.

        Raises:
            ConflictHierarchyError: If no lowest common manager exists.
        """
        lcm: str | None = self._hierarchy.get_lowest_common_manager(
            conflict.positions[0].agent_id,
            conflict.positions[1].agent_id,
        )
        for pos in conflict.positions[2:]:
            if lcm is None:
                break
            lcm = self._hierarchy.get_lowest_common_manager(lcm, pos.agent_id)
        logger.debug(
            CONFLICT_LCM_LOOKUP,
            conflict_id=str(conflict.id),
            agents=[p.agent_id for p in conflict.positions],
            lcm=lcm,
        )
        if lcm is None:
            msg = "No shared manager for conflict participants -- cannot select judge"
            logger.warning(
                CONFLICT_HIERARCHY_ERROR,
                conflict_id=str(conflict.id),
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
            reasoning=_seniority_reasoning(best, no_hierarchy=False),
        )


def _seniority_reasoning(best: ConflictPosition, *, no_hierarchy: bool) -> str:
    """Build the authority-fallback reasoning line.

    Returns:
        The seniority-based reasoning, noting when no hierarchy tiebreaker
        was available.
    """
    qualifier = " (no hierarchy)" if no_hierarchy else ""
    return (
        f"Debate fallback: authority-based judging{qualifier} -- "
        f"{best.agent_id} ({best.agent_role}) has highest authority"
    )
