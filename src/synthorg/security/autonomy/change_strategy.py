"""Human-only promotion strategy -- the default autonomy change strategy."""

from datetime import UTC, datetime

from synthorg.core.enums import AutonomyLevel, DowngradeReason, compare_autonomy
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.security import (
    SECURITY_AUTONOMY_DOWNGRADE_TRIGGERED,
    SECURITY_AUTONOMY_PROMOTION_DENIED,
    SECURITY_AUTONOMY_PROMOTION_REQUESTED,
    SECURITY_AUTONOMY_RECOVERY_REQUESTED,
)
from synthorg.security.autonomy.models import AutonomyOverride

logger = get_logger(__name__)

# Mapping from DowngradeReason to the resulting autonomy level.
_DOWNGRADE_MAP: dict[DowngradeReason, AutonomyLevel] = {
    DowngradeReason.HIGH_ERROR_RATE: AutonomyLevel.SUPERVISED,
    DowngradeReason.BUDGET_EXHAUSTED: AutonomyLevel.SUPERVISED,
    DowngradeReason.RISK_BUDGET_EXHAUSTED: AutonomyLevel.SUPERVISED,
    DowngradeReason.SECURITY_INCIDENT: AutonomyLevel.LOCKED,
}

# Validate exhaustiveness at module load time.
_missing_reasons = set(DowngradeReason) - set(_DOWNGRADE_MAP)
if _missing_reasons:
    _msg = f"_DOWNGRADE_MAP missing entries for: {_missing_reasons}"
    raise RuntimeError(_msg)


class HumanOnlyPromotionStrategy:
    """Default strategy: promotions and recovery always require human approval.

    Downgrades are applied immediately based on the reason:
    - ``HIGH_ERROR_RATE`` -> SUPERVISED (or current if more restrictive)
    - ``BUDGET_EXHAUSTED`` -> SUPERVISED (or current if more restrictive)
    - ``RISK_BUDGET_EXHAUSTED`` -> SUPERVISED (or current if more restrictive)
    - ``SECURITY_INCIDENT`` -> LOCKED

    Downgrades never *increase* autonomy: if the agent is already at
    LOCKED, a HIGH_ERROR_RATE event keeps it at LOCKED (not SUPERVISED).

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
        logger.info(
            SECURITY_AUTONOMY_PROMOTION_DENIED,
            agent_id=agent_id,
            target=target.value,
            reason="human approval required",
        )
        return False

    def auto_downgrade(
        self,
        agent_id: NotBlankStr,
        reason: DowngradeReason,
        current_level: AutonomyLevel | None = None,
    ) -> AutonomyLevel:
        """Immediately downgrade to a level determined by the reason.

        Args:
            agent_id: The agent to downgrade.
            reason: Why the downgrade is happening.
            current_level: The agent's current effective autonomy level.
                Used as ``original_level`` when no prior override exists.
                Defaults to the company default (SEMI) if not provided.

        Returns:
            The new autonomy level after downgrade.
        """
        target_level = _DOWNGRADE_MAP[reason]
        existing = self._overrides.get(agent_id)
        original = (
            existing.original_level
            if existing
            else (current_level or AutonomyLevel.SEMI)
        )

        # Never increase autonomy -- if the agent is already at or below
        # the target level, keep the current (more restrictive) level.
        effective_current = existing.current_level if existing else original
        new_level = (
            effective_current
            if compare_autonomy(effective_current, target_level) <= 0
            else target_level
        )

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
            SECURITY_AUTONOMY_DOWNGRADE_TRIGGERED,
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
        """Deny all recovery requests -- requires human approval.

        Args:
            agent_id: The agent requesting recovery.

        Returns:
            Always ``False``.
        """
        logger.info(
            SECURITY_AUTONOMY_RECOVERY_REQUESTED,
            agent_id=agent_id,
        )
        return False

    def get_override(self, agent_id: NotBlankStr) -> AutonomyOverride | None:
        """Return the active override for an agent, if any.

        Args:
            agent_id: The agent to look up.

        Returns:
            The override record, or ``None`` if no override exists.
        """
        return self._overrides.get(agent_id)

    def clear_override(self, agent_id: NotBlankStr) -> bool:
        """Remove an override (used after human recovery approval).

        Args:
            agent_id: The agent whose override to clear.

        Returns:
            ``True`` if an override was removed, ``False`` if none existed.
        """
        return self._overrides.pop(agent_id, None) is not None
