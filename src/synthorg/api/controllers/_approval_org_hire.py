# module-kind: controller
"""Org-hire flow for the approvals controller.

The step that makes an approved hire real. Without it the pipeline stopped at
the approval row: nothing flipped the request out of PENDING and nothing ever
called ``instantiate_agent``, so a human saying yes registered nobody.
"""

from typing import Final

from synthorg.api.controllers._conversational_resume import _reread_approval_item
from synthorg.api.state import AppState
from synthorg.core.approval import ApprovalItem
from synthorg.hr.enums import HiringRequestStatus
from synthorg.hr.errors import HiringError
from synthorg.hr.hiring_service import HiringService
from synthorg.hr.models import HiringRequest
from synthorg.hr.state import hiring_service_of
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.hr import (
    HR_HIRING_ALREADY_REGISTERED,
    HR_HIRING_APPROVED,
    HR_HIRING_INSTANTIATION_FAILED,
    HR_HIRING_MODEL_CHOSEN,
    HR_HIRING_REJECTED,
    HR_HIRING_REQUEST_NOT_FOUND,
)
from synthorg.security.autonomy.enums import ActionType

logger = get_logger(__name__)

#: Statuses a decision has already been applied to, so re-applying one is
#: not a retry but a refusal. APPROVED belongs here even though no agent
#: exists yet: `approve_request` persists it before instantiation runs, so an
#: instantiation that fails leaves the decision landed and the hire queued for
#: the staffing reconciler. Without it, the decision rollback that failure
#: triggers put the approval back to PENDING while the request stayed
#: APPROVED, and every operator retry then fell through to `approve_request`,
#: which refuses a request that is not awaiting a decision: a 500 on each
#: press until the reconciler happened to finish the hire.
_SETTLED_HIRING_STATUSES: Final[frozenset[HiringRequestStatus]] = frozenset(
    {
        HiringRequestStatus.APPROVED,
        HiringRequestStatus.INSTANTIATED,
        HiringRequestStatus.REJECTED,
    }
)


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

    if request.status in _SETTLED_HIRING_STATUSES:
        # The decision already landed. This is the crash-recovery drain
        # re-dispatching a marker that outlived the work it bracketed (the
        # process died between hiring the agent and clearing the marker).
        # Answering "owned, and finished" lets the outbox retire the marker;
        # falling through would call a decision method that refuses a settled
        # request, and the marker would be retried at every boot forever.
        logger.info(
            HR_HIRING_ALREADY_REGISTERED,
            approval_id=approval_id,
            request_id=str(request.id),
            request_status=request.status.value,
            note="re-dispatched decision; the hire is already settled",
        )
        return True

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

    # Before the approval transition, so the request the approve path
    # validates is already carrying the binding the operator chose: the pick
    # is part of the decision, not an edit applied to a decided request.
    request = await _apply_chosen_model(hiring, request, item)
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


async def _apply_chosen_model(
    hiring: HiringService,
    request: HiringRequest,
    item: ApprovalItem,
) -> HiringRequest:
    """Bind the hire to the pair the operator picked on the approval.

    The override half of the model proposal. The approval offers one option
    per spend profile and the request already carries the recommended pair, so
    an approval taken without touching the options needs nothing here; a pick
    replaces it.

    The option id IS the serialised pair, so nothing maps between them and
    there is no table to fall out of step with the options it describes.

    Args:
        hiring: The pipeline holding the in-flight request.
        request: The request this approval decides.
        item: The decided approval item, carrying the operator's pick.

    Returns:
        The request carrying the chosen binding, or unchanged when the
        operator made no pick.
    """
    evidence = item.evidence_package
    chosen = evidence.chosen_option_id if evidence is not None else None
    if chosen is None or chosen == request.bound_model_ref:
        return request
    logger.info(
        HR_HIRING_MODEL_CHOSEN,
        approval_id=str(item.id),
        request_id=str(request.id),
        chosen=chosen,
        proposed=request.bound_model_ref,
    )
    return await hiring.bind_model(str(request.id), chosen)


__all__ = ["try_org_hire_resume"]
