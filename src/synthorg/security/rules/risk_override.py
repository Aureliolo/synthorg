"""SecOps risk tier reclassification override.

Provides a ``RiskTierOverride`` model for runtime risk tier overrides
and a ``SecOpsRiskClassifier`` that wraps the base ``RiskClassifier``
with override support.  Overrides have mandatory expiration and can
be revoked, with all changes audit-logged.
"""

from datetime import datetime, timedelta
from typing import Final, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, model_validator

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.security import (
    SECURITY_RISK_OVERRIDE_APPLIED,
    SECURITY_RISK_OVERRIDE_CREATED,
    SECURITY_RISK_OVERRIDE_EXPIRED,
    SECURITY_RISK_OVERRIDE_REVOKED,
)
from synthorg.security.timeout.protocol import RiskTierClassifier

logger = get_logger(__name__)

_DEFAULT_REVOKED_BY = NotBlankStr("system")

# Safety ceiling on override lifetime. A CEO-level reclassification may
# legitimately last months, so the bound is generous; its job is to reject
# absurd expiries (a mistyped year) that would silently pin a tier far into
# the future.
_MAX_OVERRIDE_DURATION_DAYS: Final[int] = 365


class RiskTierOverride(BaseModel):
    """A runtime override of an action type's risk tier.

    Overrides have mandatory expiration and can be revoked before
    expiry.  All changes are audit-logged.

    Attributes:
        id: Unique override identifier.
        action_type: The ``category:action`` string being overridden.
        original_tier: Risk tier before override.
        override_tier: New risk tier.
        reason: Justification for the override.
        created_by: User ID of the creator.
        created_at: When the override was created.
        expires_at: When the override expires (must be after created_at).
        revoked_at: When revoked (None if active).
        revoked_by: Who revoked it (None if active).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr
    action_type: NotBlankStr
    original_tier: ApprovalRiskLevel
    override_tier: ApprovalRiskLevel
    reason: NotBlankStr
    created_by: NotBlankStr
    created_at: AwareDatetime
    expires_at: AwareDatetime
    revoked_at: AwareDatetime | None = None
    revoked_by: NotBlankStr | None = None

    @model_validator(mode="after")
    def _validate_expiry(self) -> Self:
        """Ensure expires_at is after created_at.

        Returns:
            The validated override.

        Raises:
            ValueError: If ``expires_at`` is not after ``created_at``.
        """
        if self.expires_at <= self.created_at:
            msg = "expires_at must be after created_at"
            raise ValueError(msg)
        if self.expires_at - self.created_at > timedelta(
            days=_MAX_OVERRIDE_DURATION_DAYS
        ):
            msg = (
                "expires_at must be within "
                f"{_MAX_OVERRIDE_DURATION_DAYS} days of created_at"
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_different_tiers(self) -> Self:
        """Reject overrides that don't change the tier.

        Returns:
            The validated override.

        Raises:
            ValueError: If ``override_tier`` equals ``original_tier``.
        """
        if self.original_tier == self.override_tier:
            msg = "override_tier must differ from original_tier"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_revocation_pair(self) -> Self:
        """Enforce that revoked_at and revoked_by are both or neither set.

        Returns:
            The validated override.

        Raises:
            ValueError: If only one of ``revoked_at`` / ``revoked_by``
                is set.
        """
        has_at = self.revoked_at is not None
        has_by = self.revoked_by is not None
        if has_at != has_by:
            msg = "revoked_at and revoked_by must both be set or both be None"
            raise ValueError(msg)
        return self

    def is_active(self, now: datetime) -> bool:
        """Return whether the override is neither revoked nor expired.

        Args:
            now: The current time (a timezone-aware ``datetime``), read
                through the caller's ``Clock`` seam so activity evaluation
                is deterministic in tests.

        Returns:
            True if the override has not been revoked and ``now`` is
            before ``expires_at``.
        """
        if self.revoked_at is not None:
            return False
        return now < self.expires_at


class SecOpsRiskClassifier:
    """Risk classifier with runtime override support.

    Wraps a base ``RiskClassifier`` and checks for active,
    non-expired, non-revoked overrides before falling back to
    the base classification.

    When multiple active overrides exist for the same action type,
    the last one added wins.

    The ``_overrides`` list grows for the lifetime of the classifier:
    revoked and expired entries are retained (never pruned) so the
    audit trail and ``revoke_override`` lookups stay stable.  Callers
    that need to bound growth must reconstruct the classifier from a
    filtered set rather than relying on in-place cleanup.

    Args:
        base: The base risk classifier for fallback.
        overrides: Initial set of overrides.
        clock: Clock seam; defaults to :class:`SystemClock`.  Activity
            checks read time through it so they are deterministic under
            ``FakeClock`` in tests.
    """

    def __init__(
        self,
        *,
        base: RiskTierClassifier,
        overrides: tuple[RiskTierOverride, ...] = (),
        clock: Clock | None = None,
    ) -> None:
        self._base = base
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._overrides: list[RiskTierOverride] = sorted(
            overrides,
            key=lambda o: o.created_at,
        )

    def classify(self, action_type: str) -> ApprovalRiskLevel:
        """Return the risk level, checking overrides first.

        Active overrides (non-expired, non-revoked) take precedence.
        When multiple active overrides exist for the same action type,
        the last one takes precedence.  Falls back to the base
        classifier when no active override matches.

        Args:
            action_type: The ``category:action`` string.

        Returns:
            The assessed risk level.
        """
        now = self._clock.now()
        # Search in reverse -- last added wins.
        for override in reversed(self._overrides):
            if override.action_type != action_type:
                continue
            if not override.is_active(now):
                event = (
                    SECURITY_RISK_OVERRIDE_REVOKED
                    if override.revoked_at is not None
                    else SECURITY_RISK_OVERRIDE_EXPIRED
                )
                logger.debug(
                    event,
                    override_id=override.id,
                    action_type=action_type,
                )
                continue
            logger.debug(
                SECURITY_RISK_OVERRIDE_APPLIED,
                override_id=override.id,
                action_type=action_type,
                original_tier=override.original_tier,
                override_tier=override.override_tier,
            )
            return override.override_tier

        return self._base.classify(action_type)

    def add_override(self, override: RiskTierOverride) -> None:
        """Register a new override.

        Args:
            override: The override to add.

        Raises:
            ValueError: If an override with the same id is already
                registered (guards against a double-add from a replayed
                persist or a re-seed).
        """
        if any(existing.id == override.id for existing in self._overrides):
            msg = f"Override {override.id!r} is already registered"
            raise ValueError(msg)
        self._overrides = [*self._overrides, override]
        logger.info(
            SECURITY_RISK_OVERRIDE_CREATED,
            override_id=override.id,
            action_type=override.action_type,
            original_tier=override.original_tier,
            override_tier=override.override_tier,
        )

    def revoke_override(
        self,
        override_id: NotBlankStr,
        *,
        revoked_by: NotBlankStr = _DEFAULT_REVOKED_BY,
    ) -> RiskTierOverride | None:
        """Mark an override as revoked and return it.

        Creates a new revoked copy of the override (frozen model).

        Args:
            override_id: ID of the override to revoke.
            revoked_by: Identity of the user or system revoking.

        Returns:
            The revoked override, or None if not found.
        """
        now = self._clock.now()
        for i, ovr in enumerate(self._overrides):
            if ovr.id == override_id and ovr.is_active(now):
                revoked = ovr.model_copy(
                    update={
                        "revoked_at": now,
                        "revoked_by": revoked_by,
                    },
                )
                new_list = list(self._overrides)
                new_list[i] = revoked
                self._overrides = new_list
                logger.info(
                    SECURITY_RISK_OVERRIDE_REVOKED,
                    override_id=override_id,
                    revoked_by=revoked_by,
                    action_type=ovr.action_type,
                    original_tier=ovr.original_tier,
                    override_tier=ovr.override_tier,
                )
                return revoked
        return None

    def active_overrides(self) -> tuple[RiskTierOverride, ...]:
        """Return all currently active overrides.

        Returns:
            Tuple of active (non-expired, non-revoked) overrides.
        """
        now = self._clock.now()
        return tuple(o for o in self._overrides if o.is_active(now))
