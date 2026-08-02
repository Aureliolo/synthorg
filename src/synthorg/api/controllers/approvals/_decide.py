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

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import LiteralString

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


def decision_dedup_key(approval_id: str, raw_key: str) -> str:
    """Derive the durable dedup key for one decision on one approval.

    Binds the approval id AND the caller's raw key into a single SHA-256
    digest, exactly as the restore endpoint does. Hashing serves two ends: a
    token reused against a DIFFERENT approval can never collide on this one's
    cached decision, and the fixed-length output can never overflow the
    255-char key column. The raw ``f"{approval_id}:{raw_key}"`` composite
    could: the path parameter accepts up to 128 characters (it is not
    validated as a UUID), so the composite reaches 347 and trips the column's
    CHECK constraint as a 500 before the id is ever resolved to a 404.

    Returns:
        The 64-char hex dedup key.
    """
    intent = f"{approval_id}\x00{raw_key}"
    return hashlib.sha256(intent.encode("utf-8", errors="replace")).hexdigest()


#: Predicate a narrowed door supplies to refuse an approval outside its scope.
#: Contract: raise to refuse; a normal return means the item is in scope. It
#: runs BEFORE the PENDING gate and before any decision is written, so an
#: out-of-scope approval is refused without being decided. (The preceding
#: store read still applies lazy TTL expiry, as it does for any reader.)
DecisionPrecondition = Callable[[ApprovalItem], None]


@dataclass(frozen=True, slots=True)
class NarrowDoor:
    """How a scope-limited door refuses an approval it will not decide.

    Both halves have to move together. A door that refuses an in-range id
    with its own fixed 404 while the preceding fetch answers an unknown id
    with the default message (which quotes the id back) still tells a caller
    which approval ids exist: the two 404s differ in the response body even
    though the status and error code match.
    """

    #: Raised for BOTH an unknown id and an in-range id this door refuses.
    not_found_message: LiteralString
    #: Refuses an approval outside the door's scope; raises to refuse.
    require: DecisionPrecondition


async def _fetch_in_scope(
    app_state: AppState,
    approval_id: str,
    door: NarrowDoor | None,
) -> ApprovalItem:
    """Fetch the approval and apply the door's scope check, if any.

    Returns:
        The approval, once it is known to be in the door's scope.

    Raises:
        NotFoundError: When no such approval exists, or the door refuses it.
            Both raise the same message when a door is supplied.
    """
    item = await _get_approval_or_404(
        app_state,
        approval_id,
        missing_message=door.not_found_message if door is not None else None,
    )
    if door is not None:
        door.require(item)
    return item


async def apply_approval(
    app_state: AppState,
    request: Request[object, object, State],
    approval_id: str,
    *,
    comment: str | None,
    chosen_option_id: str | None,
    door: NarrowDoor | None = None,
) -> ApprovalResponse:
    """Approve *approval_id*, recording the operator's reason and any pick.

    Args:
        app_state: Application state.
        request: The incoming HTTP request (for attribution and WS publish).
        approval_id: The approval to decide.
        comment: The operator's free-text reason, when the flow carries one.
        chosen_option_id: The picked option id, for a decision offering options.
        door: How a scope-limited caller refuses an approval outside its
            scope, and the fixed 404 both that refusal and an unknown id
            answer with. ``None`` for the unrestricted approvals door.

    Returns:
        The enriched approval response, built once and reused for the WS publish.

    Raises:
        ResourceNotFoundError: When no such approval exists, or ``door``
            refuses it. Both raise the same message.
        ConflictError: When the approval is no longer pending.
        ValidationError: When an options decision has no valid chosen option.
    """
    item = await _fetch_in_scope(app_state, approval_id, door)
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
    door: NarrowDoor | None = None,
) -> ApprovalResponse:
    """Reject *approval_id* with a mandatory reason.

    Args:
        app_state: Application state.
        request: The incoming HTTP request (for attribution and WS publish).
        approval_id: The approval to decide.
        reason: Why it was rejected; never optional.
        door: How a scope-limited caller refuses an approval outside its
            scope, and the fixed 404 both that refusal and an unknown id
            answer with. ``None`` for the unrestricted approvals door.

    Returns:
        The enriched approval response, built once and reused for the WS publish.

    Raises:
        ResourceNotFoundError: When no such approval exists, or ``door``
            refuses it. Both raise the same message.
        ConflictError: When the approval is no longer pending.
    """
    item = await _fetch_in_scope(app_state, approval_id, door)
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


__all__ = [
    "DecisionPrecondition",
    "NarrowDoor",
    "apply_approval",
    "apply_rejection",
    "decision_dedup_key",
]
