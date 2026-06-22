# module-kind: service
"""SecOps risk-tier override service.

Bridges the durable :class:`RiskOverrideRepository` with the live
:class:`SecOpsRiskClassifier` that the tiered approval-timeout policy
consults. ``create`` persists a new override and applies it to the live
classifier in one step; ``revoke`` does the inverse. The base classifier
is consulted to stamp each override's ``original_tier`` so the audit
record captures what the tier was before the override took effect.
"""

from datetime import datetime
from uuid import uuid4

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.core.clock import Clock
from synthorg.core.domain_errors import ConflictError
from synthorg.core.types import NotBlankStr
from synthorg.persistence.risk_override_protocol import RiskOverrideRepository
from synthorg.security.rules.risk_override import (
    RiskTierOverride,
    SecOpsRiskClassifier,
)
from synthorg.security.timeout.protocol import RiskTierClassifier


class RiskOverrideService:
    """Create, revoke, and list SecOps risk-tier overrides.

    Args:
        repo: Durable override repository (audit artefact store).
        classifier: The live :class:`SecOpsRiskClassifier` the tiered
            timeout policy evaluates; mutated in step with the repo.
        base_classifier: The wrapped base classifier, consulted to stamp
            each override's ``original_tier``.
        clock: Time source for ``created_at`` / ``revoked_at`` stamps.
    """

    def __init__(
        self,
        *,
        repo: RiskOverrideRepository,
        classifier: SecOpsRiskClassifier,
        base_classifier: RiskTierClassifier,
        clock: Clock,
    ) -> None:
        self._repo = repo
        self._classifier = classifier
        self._base = base_classifier
        self._clock = clock

    async def create(
        self,
        *,
        action_type: NotBlankStr,
        override_tier: ApprovalRiskLevel,
        reason: NotBlankStr,
        created_by: NotBlankStr,
        expires_at: datetime,
    ) -> RiskTierOverride:
        """Persist a new override and apply it to the live classifier.

        Args:
            action_type: The ``category:action`` string to reclassify.
            override_tier: The new risk tier.
            reason: Justification for the override (audit-logged).
            created_by: Identity of the operator creating the override.
            expires_at: Mandatory expiry (must be after ``created_at``).

        Returns:
            The persisted, now-active override.

        Raises:
            ConflictError: When ``override_tier`` equals the action's
                current base tier (the override would be a no-op).
        """
        original_tier = self._base.classify(str(action_type))
        if original_tier == override_tier:
            msg = (
                f"override_tier {override_tier.value!r} equals the current "
                f"base tier for {action_type!r}; the override is a no-op"
            )
            raise ConflictError(msg)
        override = RiskTierOverride(
            id=NotBlankStr(str(uuid4())),
            action_type=action_type,
            original_tier=original_tier,
            override_tier=override_tier,
            reason=reason,
            created_by=created_by,
            created_at=self._clock.now(),
            expires_at=expires_at,
        )
        await self._repo.save(override)
        self._classifier.add_override(override)
        return override

    async def revoke(
        self,
        override_id: NotBlankStr,
        *,
        revoked_by: NotBlankStr,
    ) -> RiskTierOverride | None:
        """Revoke an active override in the repo and the classifier.

        Persists before mutating the in-memory classifier so a repo
        failure cannot leave the live classifier ahead of the durable
        store (the inverse ordering of ``create``).

        Args:
            override_id: The override to revoke.
            revoked_by: Identity performing the revocation.

        Returns:
            The revoked override, or ``None`` when no active override
            with that id exists.
        """
        target = next(
            (o for o in self._classifier.active_overrides() if o.id == override_id),
            None,
        )
        if target is None:
            return None
        revoked_at = self._clock.now()
        await self._repo.revoke(
            override_id,
            revoked_by=revoked_by,
            revoked_at=revoked_at,
        )
        self._classifier.revoke_override(override_id, revoked_by=revoked_by)
        return target.model_copy(
            update={"revoked_at": revoked_at, "revoked_by": revoked_by},
        )

    def list_active(self) -> tuple[RiskTierOverride, ...]:
        """Return the currently active overrides.

        Returns:
            Tuple of non-expired, non-revoked overrides.
        """
        return self._classifier.active_overrides()


__all__ = ["RiskOverrideService"]
