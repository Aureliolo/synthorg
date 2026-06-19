# module-kind: service
"""Approval-store operations for the promotion service.

Defense-in-depth verification that a promotion request's stored
approval is genuinely approved, plus creation of approval items for
promotions that require human review. Both take the approval store
explicitly so the orchestrator stays free of approval-store wiring
detail.
"""

from datetime import UTC, datetime
from uuid import UUID, uuid4

from synthorg.approval.enums import ApprovalRiskLevel, ApprovalStatus
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.core.types import NotBlankStr
from synthorg.hr.errors import (
    PromotionApprovalRequiredError,
    PromotionError,
)
from synthorg.hr.promotion.models import PromotionEvaluation, PromotionRequest
from synthorg.observability import get_logger
from synthorg.observability.events.promotion import (
    PROMOTION_APPROVAL_SUBMITTED,
    PROMOTION_REJECTED,
)

logger = get_logger(__name__)


async def verify_approval(
    request: PromotionRequest,
    *,
    approval_store: ApprovalStoreProtocol | None,
) -> None:
    """Verify approval status from store (defense-in-depth).

    If the request has an approval_id and an approval store is
    configured, verify that the stored approval is actually approved.
    Prevents crafted requests from bypassing human approval gates.

    Raises:
        PromotionApprovalRequiredError: If the related operation fails.
    """
    if request.approval_id is None or approval_store is None:
        return

    item = await approval_store.get(request.approval_id)
    if item is None or item.status != ApprovalStatus.APPROVED:
        msg = (
            f"Approval {request.approval_id!r} not found or "
            f"not approved in approval store"
        )
        logger.warning(
            PROMOTION_REJECTED,
            agent_id=request.agent_id,
            approval_id=request.approval_id,
            error=msg,
        )
        raise PromotionApprovalRequiredError(msg)


async def create_approval(
    *,
    agent_id: NotBlankStr,
    evaluation: PromotionEvaluation,
    initiated_by: NotBlankStr,
    approval_store: ApprovalStoreProtocol | None,
) -> NotBlankStr:
    """Create an approval item for a promotion requiring human review.

    Returns:
        Result of type ``NotBlankStr``.

    Raises:
        PromotionError: If the related operation fails.
    """
    # Defense-in-depth: caller already checks, but guard against
    # direct invocation without an approval store.
    if approval_store is None:
        msg = "Cannot create approval: no approval store configured"
        logger.warning(
            PROMOTION_APPROVAL_SUBMITTED,
            agent_id=agent_id,
            error=msg,
        )
        raise PromotionError(msg)

    from synthorg.core.approval import ApprovalItem  # noqa: PLC0415

    approval_id = NotBlankStr(str(uuid4()))
    now = datetime.now(UTC)

    approval = ApprovalItem(
        id=UUID(approval_id),
        action_type="org:promote",
        title=(
            f"{evaluation.direction.value.title()}: "
            f"{evaluation.current_level.value} -> "
            f"{evaluation.target_level.value}"
        ),
        description=(
            f"Agent {agent_id!r} evaluated for "
            f"{evaluation.direction.value}. "
            f"Criteria met: {evaluation.criteria_met_count}/"
            f"{len(evaluation.criteria_results)}"
        ),
        requested_by=initiated_by,
        risk_level=ApprovalRiskLevel.MEDIUM,
        created_at=now,
        metadata={
            "agent_id": str(agent_id),
            "direction": evaluation.direction.value,
            "current_level": evaluation.current_level.value,
            "target_level": evaluation.target_level.value,
        },
    )
    await approval_store.add(approval)

    logger.info(
        PROMOTION_APPROVAL_SUBMITTED,
        agent_id=agent_id,
        approval_id=approval_id,
    )
    return approval_id
