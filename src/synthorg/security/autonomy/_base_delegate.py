"""Shared base-delegation mixin for wrapping autonomy strategies.

The performance-gated / budget-aware / escalation-chain strategies all
wrap an override-store-bearing base (``HumanOnlyPromotionStrategy``)
and delegate everything except ``request_promotion`` (and, for the
budget-aware strategy, ``auto_downgrade``) to it. This mixin holds the
base and provides the delegated methods so each strategy only writes
the behaviour it actually overrides.
"""

from typing import TYPE_CHECKING

from synthorg.core.types import NotBlankStr

if TYPE_CHECKING:
    from synthorg.core.autonomy_enums import AutonomyLevel
    from synthorg.security.autonomy.change_strategy import (
        HumanOnlyPromotionStrategy,
    )
    from synthorg.security.autonomy.enums import DowngradeReason
    from synthorg.security.autonomy.models import AutonomyOverride


class BaseDelegatingStrategy:
    """Delegates downgrade / recovery / override-store ops to a base.

    Args:
        base: The override-store-bearing strategy to delegate to.
    """

    def __init__(self, *, base: HumanOnlyPromotionStrategy) -> None:
        self._base = base

    def auto_downgrade(
        self,
        agent_id: NotBlankStr,
        reason: DowngradeReason,
        current_level: AutonomyLevel | None = None,
    ) -> AutonomyLevel:
        """Delegate to the base downgrade map.

        Returns:
            The agent's autonomy level after the downgrade.
        """
        return self._base.auto_downgrade(agent_id, reason, current_level)

    def request_recovery(self, agent_id: NotBlankStr) -> bool:
        """Delegate recovery to the base (human approval).

        Returns:
            ``True`` if a recovery request was recorded for the agent.
        """
        return self._base.request_recovery(agent_id)

    def get_override(
        self,
        agent_id: NotBlankStr,
    ) -> AutonomyOverride | None:
        """Delegate to the base override store.

        Returns:
            The agent's override record, or ``None`` if none is set.
        """
        return self._base.get_override(agent_id)

    def clear_override(self, agent_id: NotBlankStr) -> bool:
        """Delegate to the base override store.

        Returns:
            ``True`` if an override existed and was cleared.
        """
        return self._base.clear_override(agent_id)
