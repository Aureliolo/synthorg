"""ApproveAllGuard -- no-op fallback used when no guards are configured."""

from synthorg.engine.evolution.models import AdaptationDecision, AdaptationProposal
from synthorg.observability import get_logger
from synthorg.observability.events.evolution import EVOLUTION_GUARDS_PASSED

logger = get_logger(__name__)


class ApproveAllGuard:
    """Safe fallback that approves every proposal.

    Used by the evolution factory when the operator has disabled every
    real guard.  The approval reason makes it explicit that no guard
    evaluated the proposal so dashboards do not mistake this for a
    substantive sign-off.
    """

    @property
    def name(self) -> str:
        """Guard name."""
        return "ApproveAllGuard"

    async def evaluate(
        self,
        proposal: AdaptationProposal,
    ) -> AdaptationDecision:
        """Approve the proposal unconditionally with an audit-friendly reason.

        Returns:
            An :class:`AdaptationDecision` with ``approved=True`` and
            an audit-friendly auto-approval reason.
        """
        logger.info(
            EVOLUTION_GUARDS_PASSED,
            proposal_id=str(proposal.id),
            guard_name=self.name,
            auto_approved=True,
            fallback_reason="no_guards_configured",
        )
        return AdaptationDecision(
            proposal_id=proposal.id,
            approved=True,
            guard_name=self.name,
            reason=("No guards configured; proposal auto-approved by ApproveAllGuard"),
        )
