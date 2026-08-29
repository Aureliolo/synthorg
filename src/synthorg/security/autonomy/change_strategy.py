"""Human-only promotion strategy -- the default autonomy change strategy."""

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.security import (
    SECURITY_AUTONOMY_PROMOTION_DENIED,
    SECURITY_AUTONOMY_PROMOTION_REQUESTED,
)

logger = get_logger(__name__)


class HumanOnlyPromotionStrategy:
    """Default strategy: an agent never changes its own autonomy level.

    Latitude is granted by an operator and is never earned, so the only
    question this strategy answers is whether a promotion request is granted
    without a human, and the answer is always no.

    Lowering a grant is deliberately not here either. An operator owns the
    autonomy level, and a mechanism that quietly lowered it would be a second
    owner for that decision; the events that might have triggered one already
    have their own controls, since a run that reaches its cost ceiling parks
    itself and an action the security gate refuses is refused at the gate.
    """

    def request_promotion(
        self,
        agent_id: NotBlankStr,
        target: AutonomyLevel,
    ) -> bool:
        """Deny all promotion requests -- requires human approval.

        Args:
            agent_id: The agent requesting promotion.
            target: The desired autonomy level.

        Returns:
            Always ``False``.
        """
        logger.info(
            SECURITY_AUTONOMY_PROMOTION_REQUESTED,
            agent_id=agent_id,
            target=target.value,
        )
        # Signed audit-chain denial (security.* prefix). The requesting
        # agent is the principal; this strategy holds no persisted state
        # for promotions (it denies unconditionally), so the record is
        # emitted at the decision point.
        logger.info(
            SECURITY_AUTONOMY_PROMOTION_DENIED,
            agent_id=agent_id,
            target=target.value,
            reason="human approval required",
            principal=agent_id,
        )
        return False
