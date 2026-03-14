"""Seniority-based promotion approval strategy.

Implements D14: Junior to Mid auto-promotes, Senior+ requires human.
Demotions auto-apply when cost-saving mode is enabled (default);
when disabled, authority-reducing demotions from Senior+ require
human approval.
"""

from typing import TYPE_CHECKING

from synthorg.core.enums import SeniorityLevel, compare_seniority
from synthorg.hr.enums import PromotionDirection
from synthorg.hr.promotion.models import (
    PromotionApprovalDecision,
    PromotionEvaluation,
)
from synthorg.observability import get_logger
from synthorg.observability.events.promotion import PROMOTION_APPROVAL_DECIDED

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity
    from synthorg.hr.promotion.config import PromotionApprovalConfig

logger = get_logger(__name__)


class SeniorityApprovalStrategy:
    """Approval strategy based on seniority level.

    Promotions to levels at or above ``human_approval_from_level``
    require human approval. Demotions that save cost are auto-applied;
    demotions that reduce authority require human approval if configured.
    """

    def __init__(self, *, config: PromotionApprovalConfig) -> None:
        self._config = config

    @property
    def name(self) -> str:
        """Strategy name identifier."""
        return "seniority_approval"

    async def decide(
        self,
        *,
        evaluation: PromotionEvaluation,
        agent_identity: AgentIdentity,  # noqa: ARG002
    ) -> PromotionApprovalDecision:
        """Decide whether a promotion needs human approval.

        Args:
            evaluation: The promotion evaluation result.
            agent_identity: The agent's current identity (unused
                in this strategy).

        Returns:
            Approval decision.
        """
        if evaluation.direction == PromotionDirection.PROMOTION:
            decision = self._decide_promotion(evaluation)
        else:
            decision = self._decide_demotion(evaluation)

        logger.info(
            PROMOTION_APPROVAL_DECIDED,
            agent_id=evaluation.agent_id,
            direction=evaluation.direction.value,
            auto_approve=decision.auto_approve,
        )
        return decision

    def _decide_promotion(
        self,
        evaluation: PromotionEvaluation,
    ) -> PromotionApprovalDecision:
        """Decide approval for promotions."""
        target = evaluation.target_level
        threshold = self._config.human_approval_from_level

        needs_human = compare_seniority(target, threshold) >= 0

        if needs_human:
            return PromotionApprovalDecision(
                auto_approve=False,
                reason=(
                    f"Promotion to {target.value} requires human "
                    f"approval (threshold: {threshold.value})"
                ),
            )

        return PromotionApprovalDecision(
            auto_approve=True,
            reason=(
                f"Promotion to {target.value} auto-approved "
                f"(below {threshold.value} threshold)"
            ),
        )

    def _decide_demotion(
        self,
        evaluation: PromotionEvaluation,
    ) -> PromotionApprovalDecision:
        """Decide approval for demotions."""
        # Cost-saving demotions auto-apply
        if self._config.auto_demote_cost_saving:
            return PromotionApprovalDecision(
                auto_approve=True,
                reason=(
                    f"Demotion to {evaluation.target_level.value} "
                    f"auto-applied (cost-saving)"
                ),
            )

        # Authority-reducing demotions may need human
        if self._config.human_demote_authority:
            current = evaluation.current_level
            if compare_seniority(current, SeniorityLevel.SENIOR) >= 0:
                return PromotionApprovalDecision(
                    auto_approve=False,
                    reason=(
                        f"Demotion from {current.value} requires "
                        f"human approval (authority-reducing)"
                    ),
                )

        return PromotionApprovalDecision(
            auto_approve=True,
            reason=(f"Demotion to {evaluation.target_level.value} auto-applied"),
        )
