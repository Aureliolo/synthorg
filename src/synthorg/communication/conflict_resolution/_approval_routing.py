# module-kind: code
"""Route an escalated conflict into the generic approval queue.

Side-effecting helper extracted from :class:`HumanEscalationResolver` so
the resolver stays within its module-size budget. Best-effort: a failure
is logged and swallowed so the escalation-queue row remains the
authoritative record.
"""

import asyncio

from synthorg.approval.enums import ApprovalRiskLevel
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.communication.conflict_resolution.escalation.models import Escalation
from synthorg.communication.conflict_resolution.models import Conflict
from synthorg.core.approval import ApprovalItem
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.conflict import (
    CONFLICT_ESCALATION_APPROVAL_FAILED,
    CONFLICT_ESCALATION_APPROVAL_ROUTED,
)

logger = get_logger(__name__)

_APPROVAL_ACTION_TYPE: str = "conflict.escalation"


async def route_conflict_to_approval_store(
    approval_store: ApprovalStoreProtocol,
    escalation: Escalation,
    conflict: Conflict,
) -> None:
    """Mirror *conflict* into the generic approval queue.

    Best-effort: a failure is logged but never propagates (the
    escalation-queue row remains the authoritative record).

    Args:
        approval_store: The generic approval queue to submit to.
        escalation: The PENDING escalation row.
        conflict: The conflict awaiting a human decision.

    Raises:
        asyncio.CancelledError: Propagated so shutdown can reap the
            background task cleanly.
    """
    item = ApprovalItem(
        action_type=NotBlankStr(_APPROVAL_ACTION_TYPE),
        title=NotBlankStr(f"Conflict escalation: {conflict.subject}"),
        description=NotBlankStr(conflict.subject),
        requested_by=NotBlankStr("system:conflict-resolution"),
        risk_level=ApprovalRiskLevel.HIGH,
        created_at=escalation.created_at,
        metadata={
            "escalation_id": str(escalation.id),
            "conflict_id": str(conflict.id),
        },
    )
    try:
        await approval_store.add(item)
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            CONFLICT_ESCALATION_APPROVAL_FAILED,
            escalation_id=str(escalation.id),
            conflict_id=str(conflict.id),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    logger.info(
        CONFLICT_ESCALATION_APPROVAL_ROUTED,
        escalation_id=str(escalation.id),
        conflict_id=str(conflict.id),
        approval_id=str(item.id),
    )
