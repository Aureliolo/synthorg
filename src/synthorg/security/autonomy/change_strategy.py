"""Human-only promotion strategy -- the default autonomy change strategy."""

from datetime import UTC, datetime

from synthorg.core.autonomy_enums import (
    AutonomyLevel,
    compare_autonomy,
    step_down_autonomy,
)
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.security import (
    SECURITY_AUTONOMY_DOWNGRADE_TRIGGERED,
    SECURITY_AUTONOMY_OVERRIDE_CLEARED,
    SECURITY_AUTONOMY_PROMOTION_DENIED,
    SECURITY_AUTONOMY_PROMOTION_REQUESTED,
    SECURITY_AUTONOMY_RECOVERY_REQUESTED,
)
from synthorg.security.autonomy.enums import DowngradeReason
from synthorg.security.autonomy.models import AutonomyOverride

logger = get_logger(__name__)

# Reasons that downgrade to a fixed floor level regardless of the current
# level. HIGH_ERROR_RATE is intentionally absent: a noisy run is a graded
# signal, so it steps the agent down exactly one autonomy level rather than
# slamming it to a fixed floor (see ``_STEP_DOWN_REASONS``).
_FIXED_DOWNGRADE_MAP: dict[DowngradeReason, AutonomyLevel] = {
    DowngradeReason.BUDGET_EXHAUSTED: AutonomyLevel.SUPERVISED,
    DowngradeReason.RISK_BUDGET_EXHAUSTED: AutonomyLevel.SUPERVISED,
    DowngradeReason.SECURITY_INCIDENT: AutonomyLevel.LOCKED,
}

# Reasons that step the agent down exactly one autonomy level from its
# current effective level (FULL -> SEMI -> SUPERVISED -> LOCKED).
_STEP_DOWN_REASONS: frozenset[DowngradeReason] = frozenset(
    {DowngradeReason.HIGH_ERROR_RATE},
)

# Validate exhaustiveness at module load time: every reason is either a
# fixed-floor jump or a one-level step-down.
_uncovered_reasons = (
    set(DowngradeReason) - set(_FIXED_DOWNGRADE_MAP) - _STEP_DOWN_REASONS
)
if _uncovered_reasons:
    _msg = f"DowngradeReason values not handled: {_uncovered_reasons}"
    raise RuntimeError(_msg)


class HumanOnlyPromotionStrategy:
    """Default strategy: promotions and recovery always require human approval.

    Downgrades are applied immediately based on the reason:
    - ``HIGH_ERROR_RATE`` -> one level down from current
      (FULL -> SEMI -> SUPERVISED -> LOCKED)
    - ``BUDGET_EXHAUSTED`` -> SUPERVISED (or current if more restrictive)
    - ``RISK_BUDGET_EXHAUSTED`` -> SUPERVISED (or current if more restrictive)
    - ``SECURITY_INCIDENT`` -> LOCKED

    Downgrades never *increase* autonomy: if the agent is already at
    LOCKED, any downgrade event keeps it at LOCKED.

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
        existing = self._overrides.get(agent_id)
        original = (
            existing.original_level
            if existing
            else (current_level or AutonomyLevel.SEMI)
        )

        # Never increase autonomy -- if the agent is already at or below
        # the target level, keep the current (more restrictive) level.
        effective_current = existing.current_level if existing else original
        if reason in _STEP_DOWN_REASONS:
            # Graded downgrade: drop exactly one autonomy level from current.
            target_level = step_down_autonomy(effective_current)
        else:
            target_level = _FIXED_DOWNGRADE_MAP[reason]
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

        # INFO, not WARNING: this is the successful application of a
        # state transition (the override is now written), logged AFTER
        # the mutation. The business significance is carried by the
        # event name + the ``reason`` field, not the log level; the
        # component that DECIDED to downgrade emits its own WARNING.
        logger.info(
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
        removed = self._overrides.pop(agent_id, None)
        if removed is not None:
            # Clearing a SECURITY_INCIDENT / downgrade override restores
            # an agent toward its original autonomy level (it regains
            # previously denied action types), so the removal needs an
            # audit-trail INFO log just like the downgrade that set it.
            logger.info(
                SECURITY_AUTONOMY_OVERRIDE_CLEARED,
                agent_id=agent_id,
                original_level=removed.original_level.value,
                restored_from=removed.current_level.value,
                reason=removed.reason.value,
            )
        return removed is not None
