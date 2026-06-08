"""CompositeGuard -- chains multiple guards (ALL must approve)."""

from synthorg.engine.evolution.models import AdaptationDecision, AdaptationProposal
from synthorg.engine.evolution.protocols import AdaptationGuard
from synthorg.observability import get_logger
from synthorg.observability.events.evolution import (
    EVOLUTION_GUARD_DECISION,
    EVOLUTION_GUARDS_PASSED,
    EVOLUTION_GUARDS_REJECTED,
)

logger = get_logger(__name__)


class CompositeGuard:
    """Chains multiple guards with ALL-must-approve semantics.

    Evaluates guards sequentially; short-circuits on the first rejection.
    Returns the first rejection decision or the last approval if all pass.
    """

    def __init__(self, guards: tuple[AdaptationGuard, ...]) -> None:
        """Initialize CompositeGuard.

        Args:
            guards: Tuple of guards to evaluate in sequence.
        """
        self._guards = guards

    @property
    def name(self) -> str:
        """Return guard name."""
        return "CompositeGuard"

    async def evaluate(
        self,
        proposal: AdaptationProposal,
    ) -> AdaptationDecision:
        """Evaluate the proposal through all guards.

        Evaluates guards sequentially. Returns the first rejection or
        the last approval if all guards approve.

        Args:
            proposal: The adaptation proposal to evaluate.

        Returns:
            First rejection decision, or last approval if all pass.
        """
        last_decision = AdaptationDecision(
            proposal_id=proposal.id,
            approved=True,
            guard_name=self.name,
            reason="All guards approved",
        )

        proposal_id = str(proposal.id)
        for guard in self._guards:
            decision = await guard.evaluate(proposal)
            logger.debug(
                EVOLUTION_GUARD_DECISION,
                proposal_id=proposal_id,
                guard_name=guard.name,
                approved=decision.approved,
                reason=decision.reason,
            )
            if not decision.approved:
                # Chain-level decision emit so
                # ``EVOLUTION_GUARD_DECISION`` carries both per-guard
                # rows (above) AND the composite outcome (this row),
                # matching the constant's documented semantics.
                logger.debug(
                    EVOLUTION_GUARD_DECISION,
                    proposal_id=proposal_id,
                    guard_name=self.name,
                    approved=decision.approved,
                    reason=decision.reason,
                )
                logger.info(
                    EVOLUTION_GUARDS_REJECTED,
                    proposal_id=proposal_id,
                    guard_name=guard.name,
                    reason=decision.reason,
                )
                return decision
            last_decision = decision

        # Chain-level pass row: same constant covers per-guard plus
        # composite so dashboards can chart either partition.  The
        # composite reason "All guards approved" is reported here, not
        # the last individual guard's reason -- pulling
        # ``last_decision.reason`` would mislabel the chain outcome
        # with one guard's bookkeeping text.  ``proposal_id`` is on
        # every row so concurrent guard evaluations stay correlatable.
        logger.debug(
            EVOLUTION_GUARD_DECISION,
            proposal_id=proposal_id,
            guard_name=self.name,
            approved=last_decision.approved,
            reason="All guards approved",
        )
        logger.info(
            EVOLUTION_GUARDS_PASSED,
            proposal_id=proposal_id,
            guards_count=len(self._guards),
        )
        return last_decision
