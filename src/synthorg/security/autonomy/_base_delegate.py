"""Shared base-holding mixin for wrapping autonomy strategies.

The budget-aware and escalation-chain strategies each decide promotion
themselves and fall back to a wrapped base for the rest of that decision.
This mixin holds the base so neither has to repeat the constructor.
"""

from synthorg.security.autonomy.change_strategy import HumanOnlyPromotionStrategy


class BaseDelegatingStrategy:
    """Holds the strategy a wrapper delegates its promotion decision to.

    Args:
        base: The strategy to delegate to.
    """

    def __init__(self, *, base: HumanOnlyPromotionStrategy) -> None:
        self._base = base
