"""SecOps risk tier reclassification override.

Provides a ``RiskTierOverride`` model for runtime risk tier overrides
and a ``SecOpsRiskClassifier`` that wraps the base ``RiskClassifier``
with override support.  Overrides have mandatory expiration and can
be revoked, with all changes audit-logged.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, model_validator

from synthorg.core.enums import ApprovalRiskLevel  # noqa: TC001
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.security import (
    SECURITY_RISK_OVERRIDE_APPLIED,
    SECURITY_RISK_OVERRIDE_CREATED,
    SECURITY_RISK_OVERRIDE_EXPIRED,
    SECURITY_RISK_OVERRIDE_REVOKED,
)

if TYPE_CHECKING:
    from synthorg.security.rules.risk_classifier import RiskClassifier

logger = get_logger(__name__)

_DEFAULT_REVOKED_BY = NotBlankStr("system")


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
        """Ensure expires_at is after created_at."""
        if self.expires_at <= self.created_at:
            msg = "expires_at must be after created_at"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_different_tiers(self) -> Self:
        """Reject overrides that don't change the tier."""
        if self.original_tier == self.override_tier:
            msg = "override_tier must differ from original_tier"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_revocation_pair(self) -> Self:
        """Enforce that revoked_at and revoked_by are both or neither set."""
        has_at = self.revoked_at is not None
        has_by = self.revoked_by is not None
        if has_at != has_by:
            msg = "revoked_at and revoked_by must both be set or both be None"
            raise ValueError(msg)
        return self

    @property
    def is_active(self) -> bool:
        """True if the override is not revoked and not expired."""
        if self.revoked_at is not None:
            return False
        return datetime.now(tz=UTC) < self.expires_at


class SecOpsRiskClassifier:
    """Risk classifier with runtime override support.

    Wraps a base ``RiskClassifier`` and checks for active,
    non-expired, non-revoked overrides before falling back to
    the base classification.

    When multiple active overrides exist for the same action type,
    the last one added wins.

    Args:
        base: The base risk classifier for fallback.
        overrides: Initial set of overrides.
    """

    def __init__(
        self,
        *,
        base: RiskClassifier,
        overrides: tuple[RiskTierOverride, ...] = (),
    ) -> None:
        self._base = base
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
        # Search in reverse -- last added wins.
        for override in reversed(self._overrides):
            if override.action_type != action_type:
                continue
            if not override.is_active:
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
        """
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
        now = datetime.now(tz=UTC)
        for i, ovr in enumerate(self._overrides):
            if ovr.id == override_id and ovr.is_active:
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
        return tuple(o for o in self._overrides if o.is_active)
