# module-kind: code
"""The one place an approval decision is applied.

Both doors onto a decision (the approvals endpoints and the chat question
surface) call these two coroutines, so exactly one body resolves the decision
reason, records a chosen option, writes it durably, publishes the WebSocket
event and signals the resume. Two doors recording different things for the same
approval is the drift this exists to prevent.

Lives inside the approvals package so it can legitimately consume the
package-private decision helpers; its two exported names are public.
"""

from collections.abc import Callable
from datetime import UTC, datetime

from litestar import Request
from litestar.datastructures import State

from synthorg.api.controllers.approvals._decision_resolution import (
    record_chosen_option,
    resolve_decision_reason,
)
from synthorg.api.controllers.approvals._notify import (
    _decided_attribution,
    _resolve_decision,
    _save_decision_and_notify,
)
from synthorg.api.controllers.approvals._shared import (
    ApprovalResponse,
    _get_approval_or_404,
)
from synthorg.api.state import AppState
from synthorg.api.ws_models import WsEventType
from synthorg.approval.enums import ApprovalStatus
from synthorg.core.approval import ApprovalItem

#: Predicate a narrowed door supplies to refuse an approval outside its scope.
#: It runs BEFORE the PENDING gate and before any write, so an out-of-scope
#: approval is refused without being touched.
DecisionPrecondition = Callable[[ApprovalItem], None]


async def apply_approval(
    app_state: AppState,
    request: Request[object, object, State],
    approval_id: str,
    *,
    comment: str | None,
    chosen_option_id: str | None,
    require: DecisionPrecondition | None = None,
) -> ApprovalResponse:
    """Approve *approval_id*, recording the operator's reason and any pick.

    Args:
        app_state: Application state.
        request: The incoming HTTP request (for attribution and WS publish).
        approval_id: The approval to decide.
        comment: The operator's free-text reason, when the flow carries one.
        chosen_option_id: The picked option id, for a decision offering options.
        require: Optional precondition a narrowed door imposes on the item.

    Returns:
        The enriched approval response, built once and reused for the WS publish.

    Raises:
        ResourceNotFoundError: When no such approval exists, or ``require``
            refuses it.
        ConflictError: When the approval is no longer pending.
        ValidationError: When an options decision has no valid chosen option.
    """
    item = await _get_approval_or_404(app_state, approval_id)
    if require is not None:
        require(item)
    _resolve_decision(request, item, approval_id)
    decided_by, decided_by_user_id = _decided_attribution()
    decision_reason = resolve_decision_reason(
        item, chosen_option_id=chosen_option_id, comment=comment
    )
    now = datetime.now(UTC)
    previous_status = item.status
    update: dict[str, object] = {
        "status": ApprovalStatus.APPROVED,
        "decided_at": now,
        "decided_by": decided_by,
        "decision_reason": decision_reason,
    }
    # A decided decision fork records the operator's structured pick on the
    # evidence package so downstream reads surface it without parsing the
    # derived reason string.
    chosen_evidence = record_chosen_option(item, chosen_option_id=chosen_option_id)
    if chosen_evidence is not None:
        update["evidence_package"] = chosen_evidence
    # ``_save_decision_and_notify`` emits ``APPROVAL_STATUS_TRANSITIONED``
    # immediately after the persistence write succeeds, so a downstream
    # notification or resume-signal failure cannot strand the row in a decided
    # state without a corresponding transition entry. The log uses
    # ``decided_by_user_id`` (not username) to keep the observability stream
    # free of human-readable identifiers. It returns the enriched response
    # (built once for the WS publish) so the context is not resolved twice.
    return await _save_decision_and_notify(
        app_state,
        request,
        approval_id,
        item.model_copy(update=update),
        approved=True,
        decided_by=decided_by,
        decided_by_user_id=decided_by_user_id,
        previous_status=previous_status,
        decision_reason=decision_reason,
        ws_event=WsEventType.APPROVAL_APPROVED,
    )


async def apply_rejection(
    app_state: AppState,
    request: Request[object, object, State],
    approval_id: str,
    *,
    reason: str,
    require: DecisionPrecondition | None = None,
) -> ApprovalResponse:
    """Reject *approval_id* with a mandatory reason.

    Args:
        app_state: Application state.
        request: The incoming HTTP request (for attribution and WS publish).
        approval_id: The approval to decide.
        reason: Why it was rejected; never optional.
        require: Optional precondition a narrowed door imposes on the item.

    Returns:
        The enriched approval response, built once and reused for the WS publish.

    Raises:
        ResourceNotFoundError: When no such approval exists, or ``require``
            refuses it.
        ConflictError: When the approval is no longer pending.
    """
    item = await _get_approval_or_404(app_state, approval_id)
    if require is not None:
        require(item)
    _resolve_decision(request, item, approval_id)
    decided_by, decided_by_user_id = _decided_attribution()
    now = datetime.now(UTC)
    previous_status = item.status
    updated = item.model_copy(
        update={
            "status": ApprovalStatus.REJECTED,
            "decided_at": now,
            "decided_by": decided_by,
            "decision_reason": reason,
        },
    )
    return await _save_decision_and_notify(
        app_state,
        request,
        approval_id,
        updated,
        approved=False,
        decided_by=decided_by,
        decided_by_user_id=decided_by_user_id,
        previous_status=previous_status,
        decision_reason=reason,
        ws_event=WsEventType.APPROVAL_REJECTED,
    )


__all__ = ["DecisionPrecondition", "apply_approval", "apply_rejection"]
