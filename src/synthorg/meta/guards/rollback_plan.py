"""Rollback plan guard.

Rejects proposals that lack a valid rollback plan with at least
one operation and a validation check.
"""

from synthorg.core.types import NotBlankStr
from synthorg.meta.models import (
    GuardResult,
    GuardVerdict,
    ImprovementProposal,
)
from synthorg.observability import get_logger
from synthorg.observability.events.meta import (
    META_PROPOSAL_GUARD_PASSED,
    META_PROPOSAL_GUARD_REJECTED,
)

logger = get_logger(__name__)


class RollbackPlanGuard:
    """Rejects proposals without a valid rollback plan."""

    @property
    def name(self) -> NotBlankStr:
        """Guard name."""
        return NotBlankStr("rollback_plan")

    async def evaluate(
        self,
        proposal: ImprovementProposal,
    ) -> GuardResult:
        """Check if the proposal has a valid rollback plan.

        Args:
            proposal: The proposal to evaluate.

        Returns:
            Guard result with PASSED or REJECTED verdict.
        """
        plan = proposal.rollback_plan

        if not plan.operations:
            reason = "Rollback plan has no operations"
            logger.info(
                META_PROPOSAL_GUARD_REJECTED,
                guard=self.name,
                proposal_id=str(proposal.id),
                reason=reason,
            )
            return GuardResult(
                guard_name=self.name,
                verdict=GuardVerdict.REJECTED,
                reason=reason,
            )

        if not plan.validation_check.strip():
            reason = "Rollback plan has empty validation check"
            logger.info(
                META_PROPOSAL_GUARD_REJECTED,
                guard=self.name,
                proposal_id=str(proposal.id),
                reason=reason,
            )
            return GuardResult(
                guard_name=self.name,
                verdict=GuardVerdict.REJECTED,
                reason=reason,
            )

        logger.info(
            META_PROPOSAL_GUARD_PASSED,
            guard=self.name,
            proposal_id=str(proposal.id),
        )
        return GuardResult(
            guard_name=self.name,
            verdict=GuardVerdict.PASSED,
        )
