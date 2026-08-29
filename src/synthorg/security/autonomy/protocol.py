"""Autonomy change strategy protocol (see ``docs/design/security.md``, D7)."""

from typing import Protocol, runtime_checkable

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.types import NotBlankStr


# HumanOnlyPromotionStrategy impl in autonomy/change_strategy.py;
# pluggable promotion strategy seam.
@runtime_checkable
class AutonomyChangeStrategy(Protocol):
    """Strategy for deciding a runtime autonomy promotion request.

    Promotion is the only direction on this seam. An operator grants a
    level and nothing in the runtime lowers one, so there is no downgrade
    or recovery to implement.
    """

    def request_promotion(
        self,
        agent_id: NotBlankStr,
        target: AutonomyLevel,
    ) -> bool:
        """Request a promotion to a higher autonomy level.

        Args:
            agent_id: The agent requesting promotion.
            target: The desired autonomy level.

        Returns:
            ``True`` if the promotion is immediately granted,
            ``False`` if it requires human approval.
        """
        ...
