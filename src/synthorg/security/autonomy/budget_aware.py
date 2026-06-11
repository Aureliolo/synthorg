"""Budget-aware autonomy promotion strategy (REWORK #9).

Denies promotion while risk-unit budget headroom is below a warn
fraction (granting more autonomy under budget stress would let an
agent burn the remaining risk budget unattended); otherwise delegates
the promotion decision to the wrapped base strategy. Downgrade /
recovery / override-store ops delegate to the base.
"""

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.security import (
    SECURITY_AUTONOMY_PROMOTION_DENIED,
    SECURITY_AUTONOMY_PROMOTION_REQUESTED,
)
from synthorg.security.autonomy._base_delegate import BaseDelegatingStrategy
from synthorg.security.autonomy.change_strategy import HumanOnlyPromotionStrategy
from synthorg.security.autonomy.signals import RiskBudgetSignalProvider

logger = get_logger(__name__)


class BudgetAwarePromotionStrategy(BaseDelegatingStrategy):
    """Deny promotion while risk-budget headroom is below the warn line.

    Args:
        base: Override-store-bearing strategy that downgrade /
            recovery / override-store ops (and the in-budget promotion
            decision) delegate to.
        risk_budget_signal: Supplies remaining risk-budget headroom.
        warn_fraction: Promotion is denied while headroom is strictly
            below this fraction.
    """

    def __init__(
        self,
        *,
        base: HumanOnlyPromotionStrategy,
        risk_budget_signal: RiskBudgetSignalProvider,
        warn_fraction: float,
    ) -> None:
        super().__init__(base=base)
        self._risk_budget_signal = risk_budget_signal
        self._warn_fraction = warn_fraction

    def request_promotion(
        self,
        agent_id: NotBlankStr,
        target: AutonomyLevel,
    ) -> bool:
        """Deny under budget stress; otherwise delegate to the base.

        Returns:
            ``False`` when risk-budget headroom is below the warn
            fraction; otherwise the base strategy's decision.
        """
        logger.info(
            SECURITY_AUTONOMY_PROMOTION_REQUESTED,
            agent_id=agent_id,
            target=target.value,
        )
        if self._risk_budget_signal.headroom_fraction() < self._warn_fraction:
            logger.info(
                SECURITY_AUTONOMY_PROMOTION_DENIED,
                agent_id=agent_id,
                target=target.value,
                reason="risk-budget headroom below warn fraction",
            )
            return False
        return self._base.request_promotion(agent_id, target)
