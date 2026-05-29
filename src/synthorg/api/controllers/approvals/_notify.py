"""Decision-path helpers for the approvals decision controller.

Pure helper module: actor attribution, pending-state validation,
best-effort WebSocket publishing, and the persist-decide-notify
sequence (review-gate preflight, conditional persistence write,
state-transition logging, metrics, event publish, and resume signal).
The review-gate flow helpers live in ``_approval_review_gate`` and are
re-aliased here to preserve the internal API shape for callers.
"""

from datetime import UTC, datetime
from typing import Any

from litestar import Request
from litestar.channels import ChannelsPlugin

from synthorg._core.features import require_service
from synthorg.api.channels import CHANNEL_APPROVALS, get_channels_plugin
from synthorg.api.controllers._approval_review_gate import (
    preflight_review_gate,
    signal_resume_intent,
    try_mid_execution_resume,
    try_review_gate_transition,
)
from synthorg.api.state import AppState
from synthorg.api.ws_models import WsEvent, WsEventType
from synthorg.approval.state import ApprovalStateSlice
from synthorg.core.actor_context import require_actor
from synthorg.core.approval import ApprovalItem
from synthorg.core.auth.models import AuthenticatedUser
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import (
    ConflictError,
    UnauthorizedError,
)
from synthorg.core.enums import ApprovalStatus
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_APPROVAL_CONFLICT,
    API_APPROVAL_PUBLISH_FAILED,
)
from synthorg.observability.events.approval_gate import (
    APPROVAL_STATUS_TRANSITIONED,
)
from synthorg.observability.events.security import (
    SECURITY_APPROVAL_APPROVED,
    SECURITY_APPROVAL_REJECTED,
    SECURITY_AUTH_FAILED,
)
from synthorg.observability.metrics_hub import record_approval_decision

logger = get_logger(__name__)


def _require_channels_plugin(
    request: Request[Any, Any, Any],
) -> ChannelsPlugin:
    """Extract the ChannelsPlugin from the application.

    Args:
        request: The incoming request.

    Returns:
        The registered ChannelsPlugin instance.

    Raises:
        RuntimeError: If no ChannelsPlugin is registered on the app.
    """
    plugin = get_channels_plugin(request)
    if plugin is None:
        msg = "ChannelsPlugin not registered"
        logger.error(API_APPROVAL_PUBLISH_FAILED, error=msg)
        raise RuntimeError(msg)
    return plugin


def _publish_approval_event(
    request: Request[Any, Any, Any],
    event_type: WsEventType,
    item: ApprovalItem,
) -> None:
    """Publish an approval event to the approvals WebSocket channel.

    Best-effort: if the channels plugin is unavailable or not yet
    started, the error is logged and the caller continues normally.

    Args:
        request: The incoming HTTP request.
        event_type: Type of the approval event.
        item: The approval item to include in the payload.
    """
    event = WsEvent(
        event_type=event_type,
        channel=CHANNEL_APPROVALS,
        timestamp=datetime.now(UTC),
        payload={
            "approval_id": item.id,
            "status": item.status.value,
            "action_type": item.action_type,
            "risk_level": item.risk_level.value,
        },
    )
    try:
        channels_plugin = _require_channels_plugin(request)
        channels_plugin.publish(
            event.model_dump_json(),
            channels=[CHANNEL_APPROVALS],
        )
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            API_APPROVAL_PUBLISH_FAILED,
            approval_id=item.id,
            event_type=event_type.value,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


def _decided_attribution() -> tuple[str, str]:
    """Resolve ``(decided_by, decided_by_user_id)`` from the actor seam.

    RFC#3 / ADR-0003: the decision attribution comes from the actor
    bound by ``AuthContextMiddleware`` (``label`` == username,
    ``actor_id`` == immutable user id) rather than being re-derived
    from the request's auth user. Values are byte-identical to the
    previous ``auth_user.username`` / ``auth_user.user_id`` derivation,
    so the persisted row and observability stream are unchanged.

    Returns:
        ``(decided_by, decided_by_user_id)``.
    """
    actor = require_actor()
    return actor.label or actor.actor_id, actor.actor_id


def _resolve_decision(
    request: Request[Any, Any, Any],
    item: ApprovalItem,
    approval_id: str,
) -> AuthenticatedUser:
    """Validate that an approval item is pending and extract the auth user.

    Performs the shared pre-checks for approve/reject operations:
    verify the item is still in PENDING status, and look up the
    authenticated user.

    Args:
        request: The incoming HTTP request.
        item: The approval item to act on.
        approval_id: Approval identifier (for log messages).

    Returns:
        The authenticated user making the decision.

    Raises:
        UnauthorizedError: If the user is missing from the request scope.
        ConflictError: If the approval is not in PENDING status.
    """
    if item.status != ApprovalStatus.PENDING:
        msg = f"Approval {approval_id!r} is {item.status.value}, not pending"
        logger.warning(
            API_APPROVAL_CONFLICT,
            approval_id=approval_id,
            current_status=item.status.value,
        )
        raise ConflictError(msg)

    auth_user = request.scope.get("user")
    if not isinstance(auth_user, AuthenticatedUser):
        msg = "Authentication required"
        logger.warning(
            SECURITY_AUTH_FAILED,
            approval_id=approval_id,
            note="No authenticated user in request scope",
        )
        raise UnauthorizedError(msg)

    return auth_user


def _log_approval_decision(
    approval_id: str,
    *,
    approved: bool,
    decided_by: str,
) -> None:
    """Log the approval decision for observability.

    Context resumption and review-gate transitions are handled
    separately by ``_signal_resume_intent``.
    """
    event = SECURITY_APPROVAL_APPROVED if approved else SECURITY_APPROVAL_REJECTED
    logger.info(
        event,
        approval_id=approval_id,
        decided_by=decided_by,
    )


# Review-gate flow helpers live in a sibling module to keep this file
# under the 800-line limit.  Re-aliased with leading underscore here to
# preserve the internal API shape for the controller's callers.
_try_mid_execution_resume = try_mid_execution_resume
_preflight_review_gate = preflight_review_gate
_try_review_gate_transition = try_review_gate_transition
_signal_resume_intent = signal_resume_intent


async def _save_decision_and_notify(  # noqa: PLR0913
    app_state: AppState,
    request: Request[Any, Any, Any],
    approval_id: str,
    updated: ApprovalItem,
    *,
    approved: bool,
    decided_by: str,
    decided_by_user_id: str,
    previous_status: ApprovalStatus,
    decision_reason: str | None,
    ws_event: WsEventType,
) -> ApprovalItem:
    """Persist decision, publish event, log, and trigger resume.

    Args:
        app_state: Application state.
        request: The incoming HTTP request.
        approval_id: Approval identifier.
        updated: The updated approval item to persist.
        approved: Whether the action was approved.
        decided_by: Who made the decision (username -- recorded in
            the persisted approval row for the operator audit trail).
        decided_by_user_id: Immutable user id for the
            ``APPROVAL_STATUS_TRANSITIONED`` observability log so the
            log stream stays free of human-readable identifiers.
        previous_status: Status the approval was in BEFORE this
            decision; carried into the state-transition log's
            ``from_status`` kwarg.
        decision_reason: Optional reason for the decision.
        ws_event: WebSocket event type to publish.

    Returns:
        The saved approval item.

    Raises:
        ConflictError: If the approval is no longer pending.
        ForbiddenError: If the decider is the original executing agent
            (self-review preflight fails).
        NotFoundError: If the associated task no longer exists.
    """
    # Run the review-gate preflight BEFORE persisting the decision so
    # a rejected preflight never leaves a decided approval row or a
    # broadcast WebSocket event behind.
    approval = app_state.slice(ApprovalStateSlice)
    review_gate = approval.review_gate
    if review_gate is not None and updated.task_id is not None:
        await _preflight_review_gate(
            review_gate,
            approval_id,
            updated.task_id,
            decided_by=decided_by,
        )

    store = require_service(approval.store, "Approval Store")
    saved = await store.save_if_pending(updated)
    if saved is None:
        msg = "Approval is no longer pending (already decided or expired)"
        logger.warning(
            API_APPROVAL_CONFLICT,
            approval_id=approval_id,
            note=msg,
        )
        raise ConflictError(msg)

    # State-transition log fires immediately after the persistence
    # write succeeds; downstream notification or resume-signaling
    # failures cannot strand the approval row in a decided state
    # without a corresponding transition entry in the audit stream.
    logger.info(
        APPROVAL_STATUS_TRANSITIONED,
        approval_id=approval_id,
        from_status=previous_status.value,
        to_status=saved.status.value,
        # Distinct field name from the audit-trail
        # ``decided_by=<username>`` so downstream consumers can
        # disambiguate the PII-free user-id channel from the
        # operator-facing display channel without re-parsing.
        decided_by_user_id=decided_by_user_id,
    )
    record_approval_decision(outcome="approved" if approved else "rejected")

    _publish_approval_event(request, ws_event, saved)
    _log_approval_decision(
        approval_id,
        approved=approved,
        decided_by=decided_by,
    )
    await _signal_resume_intent(
        app_state,
        approval_id,
        approved=approved,
        decided_by=decided_by,
        decision_reason=decision_reason,
        task_id=saved.task_id,
    )
    return saved
