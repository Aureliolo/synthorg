"""Performance-gated autonomy promotion strategy (REWORK #9).

Grants a promotion immediately when the agent's rolling task-success
rate clears a configured threshold; otherwise defers to human
approval (returns ``False``). Downgrade, recovery, and the override
store delegate to the wrapped base strategy.
"""

from typing import TYPE_CHECKING

from synthorg.observability import get_logger
from synthorg.observability.events.security import (
    SECURITY_AUTONOMY_PROMOTION_DENIED,
    SECURITY_AUTONOMY_PROMOTION_REQUESTED,
)
from synthorg.security.autonomy._base_delegate import BaseDelegatingStrategy

if TYPE_CHECKING:
    from synthorg.core.enums import AutonomyLevel
    from synthorg.core.types import NotBlankStr
    from synthorg.security.autonomy.change_strategy import (
        HumanOnlyPromotionStrategy,
    )
    from synthorg.security.autonomy.signals import PerformanceSignalProvider

logger = get_logger(__name__)


class PerformanceGatedPromotionStrategy(BaseDelegatingStrategy):
    """Auto-grant promotion above a rolling-success-rate threshold.

    Args:
        base: Override-store-bearing strategy that downgrade /
            recovery / override-store ops delegate to.
        performance_signal: Supplies the agent's rolling success rate.
        success_threshold: Minimum success rate (``[0, 1]``) to grant.
    """

    def __init__(
        self,
        *,
        base: HumanOnlyPromotionStrategy,
        performance_signal: PerformanceSignalProvider,
        success_threshold: float,
    ) -> None:
        super().__init__(base=base)
        self._performance_signal = performance_signal
        self._success_threshold = success_threshold

    def request_promotion(
        self,
        agent_id: NotBlankStr,
        target: AutonomyLevel,
    ) -> bool:
        """Grant when rolling success rate >= threshold; else defer."""
        logger.info(
            SECURITY_AUTONOMY_PROMOTION_REQUESTED,
            agent_id=agent_id,
            target=target.value,
        )
        rate = self._performance_signal.success_rate(agent_id)
        if rate is not None and rate >= self._success_threshold:
            return True
        logger.info(
            SECURITY_AUTONOMY_PROMOTION_DENIED,
            agent_id=agent_id,
            target=target.value,
            reason="success rate below promotion threshold",
        )
        return False
