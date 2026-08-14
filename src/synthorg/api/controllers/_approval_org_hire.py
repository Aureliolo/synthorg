# module-kind: controller
"""Org-hire flow for the approvals controller.

The step that makes an approved hire real. Without it the pipeline stopped at
the approval row: nothing flipped the request out of PENDING and nothing ever
called ``instantiate_agent``, so a human saying yes registered nobody.
"""

from synthorg.api.controllers._conversational_resume import _reread_approval_item
from synthorg.api.state import AppState
from synthorg.hr.errors import HiringError
from synthorg.hr.state import hiring_service_of
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.hr import (
    HR_HIRING_APPROVED,
    HR_HIRING_INSTANTIATION_FAILED,
    HR_HIRING_REJECTED,
    HR_HIRING_REQUEST_NOT_FOUND,
)
from synthorg.security.autonomy.enums import ActionType

logger = get_logger(__name__)


async def try_org_hire_resume(
    app_state: AppState,
    approval_id: str,
    *,
    approved: bool,
    decided_by: str,
) -> bool:
    """Instantiate or cancel the hire a decided ``org:hire`` approval owns.

    Ownership is decided off the item's ``action_type``, fixed at creation,
    so every non-hiring approval reads as a miss and falls through untouched.

    Args:
        app_state: Application state carrying the hiring service.
        approval_id: The decided approval item's id.
        approved: Whether the human said yes.
        decided_by: Who decided, for the audit trail.

    Returns:
        ``True`` when this flow owns the approval, so the caller does not
        also run the parked-context or review-gate flows.

    Raises:
        HiringError: When the decided request cannot be found. A hire that
            silently did not land is exactly what this flow exists to
            prevent, so an owned approval never returns quietly.
    """
    item = await _reread_approval_item(app_state, approval_id)
    if item is None or str(item.action_type) != ActionType.ORG_HIRE.value:
        return False

    hiring = hiring_service_of(app_state)
    request = hiring.find_by_approval_id(approval_id)
    if request is None:
        msg = f"No hiring request found for approval {approval_id!r}"
        logger.error(HR_HIRING_REQUEST_NOT_FOUND, approval_id=approval_id, error=msg)
        raise HiringError(msg)

    if not approved:
        await hiring.reject_request(
            str(request.id), decided_by=decided_by, reason=item.decision_reason
        )
        logger.info(
            HR_HIRING_REJECTED,
            approval_id=approval_id,
            request_id=str(request.id),
            role=str(request.role),
        )
        return True

    approved_request = await hiring.approve_request(
        str(request.id), decided_by=decided_by
    )
    # Deliberately unguarded: a failure here strands an APPROVED request with
    # no agent, and the operator must see that rather than a 200 that hired
    # nobody. The staffing reconciler picks the request back up on its next
    # pass once whatever blocked it (an unbound new-hire pair, a registry
    # outage) is resolved.
    try:
        identity = await hiring.instantiate_agent(approved_request)
    except Exception as exc:
        logger.error(
            HR_HIRING_INSTANTIATION_FAILED,
            approval_id=approval_id,
            request_id=str(request.id),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise
    logger.info(
        HR_HIRING_APPROVED,
        approval_id=approval_id,
        request_id=str(request.id),
        agent_id=str(identity.id),
        role=str(request.role),
    )
    return True


__all__ = ["try_org_hire_resume"]
