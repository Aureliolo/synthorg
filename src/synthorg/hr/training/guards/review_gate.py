"""Review gate guard for training mode.

Routes curated training items through the existing ApprovalStore
for human review. When review is required, all items are blocked
until the approval item is approved.
"""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

from synthorg.core.approval import ApprovalItem
from synthorg.core.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.hr.training.models import (
    ContentType,
    TrainingGuardDecision,
    TrainingItem,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.training import (
    HR_TRAINING_REVIEW_GATE_CREATED,
    HR_TRAINING_REVIEW_GATE_FAILED,
)

if TYPE_CHECKING:
    from synthorg.approval.protocol import ApprovalStoreProtocol
    from synthorg.hr.training.models import TrainingPlan

logger = get_logger(__name__)

_APPROVAL_EXPIRY_HOURS = 24


class ReviewGateGuard:
    """Human-in-the-loop review gate guard.

    When ``plan.require_review`` is True, creates an ``ApprovalItem``
    in the ``ApprovalStore`` and blocks all items until approved.
    The decision's ``rejected_count`` reflects the number of items
    held for review so downstream accounting stays consistent.
    When review is not required, passes all items through unchanged.

    Args:
        approval_store: Approval store for creating review items.
    """

    def __init__(self, *, approval_store: ApprovalStoreProtocol) -> None:
        self._approval_store = approval_store

    @property
    def name(self) -> str:
        """Guard name."""
        return "review_gate"

    async def evaluate(
        self,
        items: tuple[TrainingItem, ...],
        *,
        content_type: ContentType,
        plan: TrainingPlan,
    ) -> TrainingGuardDecision:
        """Evaluate items against review gate policy.

        Args:
            items: Items to evaluate.
            content_type: Content type being evaluated.
            plan: Training plan with review settings.

        Returns:
            Decision blocking items when review required.
        """
        if not items:
            return TrainingGuardDecision(
                approved_items=(),
                rejected_count=0,
                guard_name="review_gate",
            )

        if not plan.require_review:
            return TrainingGuardDecision(
                approved_items=items,
                rejected_count=0,
                guard_name="review_gate",
            )

        # Create approval item for human review.
        now = datetime.now(UTC)
        approval_id = str(uuid4())
        approval_item = ApprovalItem(
            id=approval_id,
            action_type="training_review",
            title=(f"Training plan {plan.id} - {content_type.value} items"),
            description=(
                f"Review {len(items)} {content_type.value} "
                f"training items for agent {plan.new_agent_id}"
            ),
            requested_by="training_service",
            risk_level=ApprovalRiskLevel.MEDIUM,
            status=ApprovalStatus.PENDING,
            created_at=now,
            expires_at=now + timedelta(hours=_APPROVAL_EXPIRY_HOURS),
        )
        try:
            await self._approval_store.add(approval_item)
        except Exception as exc:
            logger.warning(
                HR_TRAINING_REVIEW_GATE_FAILED,
                plan_id=str(plan.id),
                content_type=content_type.value,
                approval_id=approval_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise

        logger.info(
            HR_TRAINING_REVIEW_GATE_CREATED,
            plan_id=str(plan.id),
            content_type=content_type.value,
            item_count=len(items),
            approval_id=approval_id,
        )

        rejection_reason = f"Held for review in approval item {approval_id}"
        return TrainingGuardDecision(
            approved_items=(),
            rejected_count=len(items),
            guard_name="review_gate",
            rejection_reasons=tuple(rejection_reason for _ in items),
            approval_item_id=approval_id,
        )
