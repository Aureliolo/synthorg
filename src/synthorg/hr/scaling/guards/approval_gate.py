"""Approval gate guard.

Routes scaling decisions through the existing ApprovalStore
for human approval. Creates ApprovalItem entries and returns
the original decisions unchanged; execution checks approval
status later.
"""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Final
from uuid import NAMESPACE_URL, UUID, uuid5

from synthorg.approval.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.core.approval import ApprovalItem
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.hr.scaling.enums import ScalingActionType
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.hr import HR_SCALING_GUARD_APPLIED

if TYPE_CHECKING:
    from synthorg.approval.protocol import ApprovalStoreProtocol
    from synthorg.hr.scaling.models import ScalingDecision

logger = get_logger(__name__)
_DEFAULT_EXPIRY_DAYS: Final[int] = 7

# Map scaling actions to approval risk levels.
_RISK_MAP: dict[ScalingActionType, ApprovalRiskLevel] = {
    ScalingActionType.HIRE: ApprovalRiskLevel.MEDIUM,
    ScalingActionType.PRUNE: ApprovalRiskLevel.CRITICAL,
}


class ApprovalGateGuard:
    """Routes decisions through the approval system.

    Creates ``ApprovalItem`` entries in the ``ApprovalStore`` for
    each decision and returns the original decisions unchanged
    (execution will check approval status later).

    Args:
        approval_store: Existing approval store to use.
        expiry_days: Days until approval items expire.
    """

    def __init__(
        self,
        *,
        approval_store: ApprovalStoreProtocol,
        expiry_days: int = _DEFAULT_EXPIRY_DAYS,
    ) -> None:
        if expiry_days <= 0:
            msg = f"expiry_days must be > 0, got {expiry_days}"
            logger.warning(
                HR_SCALING_GUARD_APPLIED,
                guard="approval_gate",
                action="init_validation_failed",
                expiry_days=expiry_days,
            )
            raise ValueError(msg)
        self._store = approval_store
        self._expiry_days = expiry_days

    @property
    def name(self) -> NotBlankStr:
        """Guard identifier."""
        return NotBlankStr("approval_gate")

    async def filter(
        self,
        decisions: tuple[ScalingDecision, ...],
    ) -> tuple[ScalingDecision, ...]:
        """Create approval items for all actionable decisions.

        This is a side-effect-only guard: it creates ``ApprovalItem``
        entries in the ``ApprovalStore`` and returns the decisions
        unchanged so downstream code can execute approved actions
        (or defer execution pending human approval). NO_OP and HOLD
        decisions are skipped since they do not require approval.

        Approval-store write failures for a single decision are
        logged but do not abort the whole chain -- the decision is
        dropped (it has no corresponding approval entry and cannot
        be executed safely).

        Args:
            decisions: Incoming decisions.

        Returns:
            Decisions that either do not need approval or had an
            approval item successfully created.
        """
        surviving: list[ScalingDecision] = []
        for decision in decisions:
            if decision.action_type in {
                ScalingActionType.NO_OP,
                ScalingActionType.HOLD,
            }:
                surviving.append(decision)
                continue

            risk = _RISK_MAP.get(
                decision.action_type,
                ApprovalRiskLevel.MEDIUM,
            )

            title = (
                f"Scaling: {decision.action_type.value} "
                f"({decision.source_strategy.value})"
            )

            now = datetime.now(UTC)
            # Deterministic id from a SEMANTIC key (not the transient
            # decision.id) so replays across evaluation cycles reuse
            # the same approval item instead of enqueuing duplicates.
            semantic_key = "|".join(
                [
                    decision.action_type.value,
                    decision.source_strategy.value,
                    str(decision.target_agent_id or ""),
                    str(decision.target_role or ""),
                    str(decision.target_department or ""),
                    ",".join(sorted(str(s) for s in decision.target_skills)),
                ],
            )
            approval_id = str(
                uuid5(NAMESPACE_URL, f"scaling:{semantic_key}"),
            )
            item = ApprovalItem(
                id=UUID(approval_id),
                action_type=f"scaling:{decision.action_type.value}",
                title=title,
                description=str(decision.rationale),
                requested_by="scaling_service",
                risk_level=risk,
                status=ApprovalStatus.PENDING,
                metadata={
                    "scaling_decision_id": str(decision.id),
                    "scaling_semantic_key": semantic_key,
                    "source_strategy": str(decision.source_strategy),
                    "confidence": str(decision.confidence),
                },
                created_at=now,
                expires_at=now + timedelta(days=self._expiry_days),
            )

            try:
                await self._store.add(item)
            except Exception as exc:
                reraise_critical(exc)
                log_exception_redacted(
                    logger,
                    HR_SCALING_GUARD_APPLIED,
                    exc,
                    guard="approval_gate",
                    action="approval_creation_failed",
                    decision_id=str(decision.id),
                )
                # Drop the decision: without an approval item it
                # cannot be safely executed through the existing flow.
                continue

            logger.info(
                HR_SCALING_GUARD_APPLIED,
                guard="approval_gate",
                action="approval_created",
                approval_id=item.id,
                decision_id=str(decision.id),
                risk_level=risk.value,
            )
            surviving.append(decision)

        return tuple(surviving)
