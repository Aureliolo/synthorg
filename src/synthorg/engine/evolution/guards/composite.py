"""CompositeGuard -- chains multiple guards (ALL must approve)."""

from typing import TYPE_CHECKING

from synthorg.engine.evolution.models import AdaptationDecision, AdaptationProposal
from synthorg.observability import get_logger
from synthorg.observability.events.evolution import (
    EVOLUTION_GUARD_DECISION,
    EVOLUTION_GUARDS_PASSED,
    EVOLUTION_GUARDS_REJECTED,
)

logger = get_logger(__name__)

if TYPE_CHECKING:
    from synthorg.engine.evolution.protocols import AdaptationGuard


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

        for guard in self._guards:
            decision = await guard.evaluate(proposal)
            logger.debug(
                EVOLUTION_GUARD_DECISION,
                guard_name=guard.name,
                approved=decision.approved,
                reason=decision.reason,
            )
            if not decision.approved:
                logger.info(
                    EVOLUTION_GUARDS_REJECTED,
                    proposal_id=str(proposal.id),
                    guard_name=guard.name,
                    reason=decision.reason,
                )
                return decision
            last_decision = decision

        logger.info(
            EVOLUTION_GUARDS_PASSED,
            proposal_id=str(proposal.id),
            guards_count=len(self._guards),
        )
        return last_decision
