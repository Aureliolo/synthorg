"""Escalation-chain autonomy promotion strategy.

Promotion is routed through a configured ordered role chain
(supervisor -> manager -> CEO, etc.). The strategy itself never
auto-grants: it records the chain the request must traverse and
returns ``False`` (pending) -- the actual per-role approvals arrive
out-of-band through the normal approval surface, exactly like the
human-only default but with the chain made explicit for operators.
Downgrade / recovery / override-store ops delegate to the base.
"""

from typing import TYPE_CHECKING

from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.security import (
    SECURITY_AUTONOMY_PROMOTION_DENIED,
    SECURITY_AUTONOMY_PROMOTION_REQUESTED,
)
from synthorg.security.autonomy._base_delegate import BaseDelegatingStrategy

if TYPE_CHECKING:
    from synthorg.core.autonomy_enums import AutonomyLevel
    from synthorg.security.autonomy.change_strategy import (
        HumanOnlyPromotionStrategy,
    )

logger = get_logger(__name__)


class EscalationChainPromotionStrategy(BaseDelegatingStrategy):
    """Route promotion through an ordered approver-role chain.

    Args:
        base: Override-store-bearing strategy that downgrade /
            recovery / override-store ops delegate to.
        chain: Ordered approver roles the request must traverse. An
            empty chain means the request is always pending (no
            configured approvers).
    """

    def __init__(
        self,
        *,
        base: HumanOnlyPromotionStrategy,
        chain: tuple[str, ...],
    ) -> None:
        super().__init__(base=base)
        self._chain = chain

    def request_promotion(
        self,
        agent_id: NotBlankStr,
        target: AutonomyLevel,
    ) -> bool:
        """Record the chain and return pending (never auto-grants).

        Returns:
            Always ``False``; the promotion stays pending escalation-chain
            approval.
        """
        logger.info(
            SECURITY_AUTONOMY_PROMOTION_REQUESTED,
            agent_id=agent_id,
            target=target.value,
        )
        logger.info(
            SECURITY_AUTONOMY_PROMOTION_DENIED,
            agent_id=agent_id,
            target=target.value,
            reason="pending escalation-chain approval",
            chain=list(self._chain),
        )
        return False
