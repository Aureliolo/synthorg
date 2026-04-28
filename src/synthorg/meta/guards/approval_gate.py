"""Approval gate guard.

Routes proposals to the ApprovalStore for mandatory human review.
The guard is the enforcement point for the ``requires_human_review``
invariant: a proposal can only proceed if it is durably registered in
the approval queue.  When the store is unavailable or a write fails,
the guard rejects so the system fails closed instead of silently
bypassing review.
"""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, uuid5

from synthorg.core.approval import ApprovalItem
from synthorg.core.domain_errors import ConflictError
from synthorg.core.enums import ApprovalRiskLevel
from synthorg.core.types import NotBlankStr
from synthorg.meta.models import (
    GuardResult,
    GuardVerdict,
    ImprovementProposal,
    ProposalAltitude,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.meta import (
    META_PROPOSAL_GUARD_PASSED,
    META_PROPOSAL_GUARD_REJECTED,
)

if TYPE_CHECKING:
    from synthorg.approval.protocol import ApprovalStoreProtocol

logger = get_logger(__name__)

_ALTITUDE_RISK: dict[ProposalAltitude, ApprovalRiskLevel] = {
    ProposalAltitude.CONFIG_TUNING: ApprovalRiskLevel.MEDIUM,
    ProposalAltitude.ARCHITECTURE: ApprovalRiskLevel.HIGH,
    ProposalAltitude.PROMPT_TUNING: ApprovalRiskLevel.MEDIUM,
    ProposalAltitude.CODE_MODIFICATION: ApprovalRiskLevel.CRITICAL,
}

_DEFAULT_EXPIRY_DAYS = 7


class ApprovalGateGuard:
    """Routes proposals to the approval store for human review.

    ``evaluate()`` returns PASSED only when the proposal is durably
    registered in the approval queue: on first evaluation via
    ``ApprovalStoreProtocol.add()``, and on replay (same deterministic
    ``approval_id``) via the ``ConflictError`` branch which treats the
    duplicate as idempotent success.  When no store is configured, or
    any non-duplicate write error surfaces, the guard returns
    REJECTED so the meta loop fails closed instead of silently
    bypassing mandatory human review.

    Args:
        approval_store: The approval store instance.  When ``None``
            the guard always rejects -- callers must wire a concrete
            store before enabling the meta-loop pipeline.
        expiry_days: Days until approval items expire. Must be > 0.
    """

    def __init__(
        self,
        *,
        approval_store: ApprovalStoreProtocol | None = None,
        expiry_days: int = _DEFAULT_EXPIRY_DAYS,
    ) -> None:
        if expiry_days <= 0:
            msg = f"expiry_days must be > 0, got {expiry_days}"
            raise ValueError(msg)
        self._store = approval_store
        self._expiry_days = expiry_days

    @property
    def name(self) -> NotBlankStr:
        """Guard name."""
        return NotBlankStr("approval_gate")

    async def evaluate(
        self,
        proposal: ImprovementProposal,
    ) -> GuardResult:
        """Register proposal in approval store.

        Args:
            proposal: The proposal to route for approval.

        Returns:
            PASSED when the approval item is durably registered
            (newly created, or already present from a replay);
            REJECTED when no store is configured or the write fails
            with a non-duplicate error.
        """
        risk = _ALTITUDE_RISK.get(
            proposal.altitude,
            ApprovalRiskLevel.MEDIUM,
        )
        proposal_id_str = str(proposal.id)

        if self._store is None:
            reason = (
                "Approval store not configured; cannot register proposal "
                "for mandatory human review."
            )
            logger.warning(
                META_PROPOSAL_GUARD_REJECTED,
                guard=self.name,
                proposal_id=proposal_id_str,
                risk_level=risk.value,
                altitude=proposal.altitude.value,
                reason="approval_store_not_configured",
            )
            return GuardResult(
                guard_name=self.name,
                verdict=GuardVerdict.REJECTED,
                reason=NotBlankStr(reason),
            )

        now = datetime.now(UTC)
        # Deterministic id from the proposal id so replays across
        # evaluation cycles reuse the same approval entry rather than
        # enqueueing duplicates.
        approval_id = str(
            uuid5(NAMESPACE_URL, f"proposal:{proposal_id_str}"),
        )
        item = ApprovalItem(
            id=approval_id,
            action_type=f"proposal:{proposal.altitude.value}",
            title=proposal.title,
            description=proposal.description,
            requested_by="meta_improvement_service",
            risk_level=risk,
            created_at=now,
            expires_at=now + timedelta(days=self._expiry_days),
            metadata={
                "proposal_id": proposal_id_str,
                "altitude": proposal.altitude.value,
                "source_rule": str(proposal.source_rule or ""),
                "confidence": f"{proposal.confidence:.4f}",
            },
        )

        try:
            await self._store.add(item)
        except MemoryError, RecursionError:
            raise
        except ConflictError:
            # Replay: the deterministic approval_id already exists, so
            # the proposal is durably registered.  Treat as idempotent
            # success -- not a silent bypass.
            logger.info(
                META_PROPOSAL_GUARD_PASSED,
                guard=self.name,
                proposal_id=proposal_id_str,
                approval_id=approval_id,
                risk_level=risk.value,
                altitude=proposal.altitude.value,
                persisted=True,
                replay=True,
            )
            return GuardResult(
                guard_name=self.name,
                verdict=GuardVerdict.PASSED,
            )
        except Exception as exc:
            reason = (
                f"Approval store write failed "
                f"({type(exc).__name__}); proposal not persisted."
            )
            logger.warning(
                META_PROPOSAL_GUARD_REJECTED,
                guard=self.name,
                proposal_id=proposal_id_str,
                approval_id=approval_id,
                risk_level=risk.value,
                altitude=proposal.altitude.value,
                reason="approval_store_write_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return GuardResult(
                guard_name=self.name,
                verdict=GuardVerdict.REJECTED,
                reason=NotBlankStr(reason),
            )

        logger.info(
            META_PROPOSAL_GUARD_PASSED,
            guard=self.name,
            proposal_id=proposal_id_str,
            approval_id=approval_id,
            risk_level=risk.value,
            altitude=proposal.altitude.value,
            persisted=True,
        )
        return GuardResult(
            guard_name=self.name,
            verdict=GuardVerdict.PASSED,
        )
