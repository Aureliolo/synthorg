"""Human-only promotion strategy — the default autonomy change strategy."""

from datetime import UTC, datetime

from ai_company.core.enums import AutonomyLevel, DowngradeReason
from ai_company.core.types import NotBlankStr  # noqa: TC001
from ai_company.observability import get_logger
from ai_company.observability.events.autonomy import (
    AUTONOMY_DOWNGRADE_TRIGGERED,
    AUTONOMY_PROMOTION_DENIED,
    AUTONOMY_PROMOTION_REQUESTED,
    AUTONOMY_RECOVERY_REQUESTED,
)
from ai_company.security.autonomy.models import AutonomyOverride

logger = get_logger(__name__)

# Mapping from DowngradeReason to the resulting autonomy level.
_DOWNGRADE_MAP: dict[DowngradeReason, AutonomyLevel] = {
    DowngradeReason.HIGH_ERROR_RATE: AutonomyLevel.SUPERVISED,
    DowngradeReason.BUDGET_EXHAUSTED: AutonomyLevel.SUPERVISED,
    DowngradeReason.SECURITY_INCIDENT: AutonomyLevel.LOCKED,
}


class HumanOnlyPromotionStrategy:
    """Default strategy: promotions and recovery always require human approval.

    Downgrades are applied immediately based on the reason:
    - ``HIGH_ERROR_RATE`` → SUPERVISED
    - ``BUDGET_EXHAUSTED`` → SUPERVISED
    - ``SECURITY_INCIDENT`` → LOCKED

    This strategy tracks active overrides in memory. In production,
    overrides should be persisted to the persistence backend.
    """

    def __init__(self) -> None:
        self._overrides: dict[str, AutonomyOverride] = {}

    def request_promotion(
        self,
        agent_id: NotBlankStr,
        target: AutonomyLevel,
    ) -> bool:
        """Deny all promotion requests — requires human approval.

        Args:
            agent_id: The agent requesting promotion.
            target: The desired autonomy level.

        Returns:
            Always ``False``.
        """
        logger.info(
            AUTONOMY_PROMOTION_REQUESTED,
            agent_id=agent_id,
            target=target.value,
        )
        logger.info(
            AUTONOMY_PROMOTION_DENIED,
            agent_id=agent_id,
            target=target.value,
            reason="human approval required",
        )
        return False

    def auto_downgrade(
        self,
        agent_id: NotBlankStr,
        reason: DowngradeReason,
    ) -> AutonomyLevel:
        """Immediately downgrade to a level determined by the reason.

        Args:
            agent_id: The agent to downgrade.
            reason: Why the downgrade is happening.

        Returns:
            The new autonomy level after downgrade.
        """
        new_level = _DOWNGRADE_MAP[reason]
        existing = self._overrides.get(agent_id)
        original = existing.original_level if existing else AutonomyLevel.SEMI

        override = AutonomyOverride(
            agent_id=agent_id,
            original_level=original,
            current_level=new_level,
            reason=reason,
            downgraded_at=datetime.now(UTC),
            requires_human_recovery=True,
        )
        self._overrides[agent_id] = override

        logger.warning(
            AUTONOMY_DOWNGRADE_TRIGGERED,
            agent_id=agent_id,
            reason=reason.value,
            new_level=new_level.value,
            original_level=original.value,
        )
        return new_level

    def request_recovery(
        self,
        agent_id: NotBlankStr,
    ) -> bool:
        """Deny all recovery requests — requires human approval.

        Args:
            agent_id: The agent requesting recovery.

        Returns:
            Always ``False``.
        """
        logger.info(
            AUTONOMY_RECOVERY_REQUESTED,
            agent_id=agent_id,
        )
        return False

    def get_override(self, agent_id: str) -> AutonomyOverride | None:
        """Return the active override for an agent, if any.

        Args:
            agent_id: The agent to look up.

        Returns:
            The override record, or ``None`` if no override exists.
        """
        return self._overrides.get(agent_id)

    def clear_override(self, agent_id: str) -> bool:
        """Remove an override (used after human recovery approval).

        Args:
            agent_id: The agent whose override to clear.

        Returns:
            ``True`` if an override was removed, ``False`` if none existed.
        """
        return self._overrides.pop(agent_id, None) is not None
