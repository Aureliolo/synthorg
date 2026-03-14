"""Promotion approval strategy protocol.

Defines the pluggable interface for deciding whether promotions
require human approval.
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity
    from synthorg.hr.promotion.models import (
        PromotionApprovalDecision,
        PromotionEvaluation,
    )


@runtime_checkable
class PromotionApprovalStrategy(Protocol):
    """Protocol for promotion approval decisions.

    Implementations determine whether a promotion/demotion can be
    auto-approved or requires human intervention.
    """

    @property
    def name(self) -> str:
        """Strategy name identifier."""
        ...

    async def decide(
        self,
        *,
        evaluation: PromotionEvaluation,
        agent_identity: AgentIdentity,
    ) -> PromotionApprovalDecision:
        """Decide whether a promotion needs human approval.

        Args:
            evaluation: The promotion evaluation result.
            agent_identity: The agent's current identity.

        Returns:
            Approval decision.
        """
        ...
