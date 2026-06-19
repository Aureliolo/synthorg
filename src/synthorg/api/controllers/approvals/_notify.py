# module-kind: code
"""Decision-path helpers for the approvals decision controller.

Pure helper module: actor attribution, pending-state validation,
best-effort WebSocket publishing, and the persist-decide-notify
sequence (review-gate preflight, conditional persistence write,
state-transition logging, metrics, event publish, and resume signal).
The review-gate flow helpers live in ``_approval_review_gate`` and are
re-aliased here to preserve the internal API shape for callers.
"""

from datetime import UTC, datetime

from litestar import Request
from litestar.channels import ChannelsPlugin
from litestar.datastructures import State

from synthorg._core.features import require_service
from synthorg.api.auth.controller_helpers import require_authenticated_user
from synthorg.api.channels import CHANNEL_APPROVALS, get_channels_plugin
from synthorg.api.controllers._approval_review_gate import (
    preflight_review_gate,
    signal_resume_intent,
    try_mid_execution_resume,
    try_review_gate_transition,
)
from synthorg.api.state import AppState
from synthorg.api.ws_models import WsEvent, WsEventType
from synthorg.approval.enums import ApprovalStatus
from synthorg.approval.state import ApprovalStateSlice
from synthorg.core.actor_context import require_actor
from synthorg.core.approval import ApprovalItem
from synthorg.core.auth.models import AuthenticatedUser
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import (
    ConflictError,
    ServiceUnavailableError,
)
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
)
from synthorg.observability.metrics_hub import record_approval_decision

logger = get_logger(__name__)


def _require_channels_plugin(
    request: Request[object, object, State],
) -> ChannelsPlugin:
    """Extract the ChannelsPlugin from the application.

    Args:
        request: The incoming request.

    Returns:
        The registered ChannelsPlugin instance.

    Raises:
        ServiceUnavailableError: If no ChannelsPlugin is registered on
            the app (the realtime notification surface is unwired).
    """
    plugin = get_channels_plugin(request)
    if plugin is None:
        msg = "ChannelsPlugin not registered"
        logger.error(
            API_APPROVAL_PUBLISH_FAILED,
            error=msg,
            error_type=ServiceUnavailableError.__name__,
        )
        raise ServiceUnavailableError(msg)
    return plugin


def _publish_approval_event(
    request: Request[object, object, State],
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
            "approval_id": str(item.id),
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
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
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
    request: Request[object, object, State],
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

    return require_authenticated_user(request)


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


async def _run_review_gate_preflight(
    app_state: AppState,
    approval_id: str,
    updated: ApprovalItem,
    *,
    decided_by: str,
) -> None:
    """Run the review-gate preflight before the decision is persisted.

    Runs BEFORE persistence so a rejected preflight never leaves a decided
    approval row or a broadcast WebSocket event behind. No-op when the review
    gate is unwired or the approval has no associated task.

    Args:
        app_state: Application state (source of the review-gate slice).
        approval_id: Approval identifier.
        updated: The approval item about to be persisted.
        decided_by: Who is making the decision.

    Raises:
        ForbiddenError: If the decider is the original executing agent
            (self-review preflight fails).
    """
    review_gate = app_state.slice(ApprovalStateSlice).review_gate
    if review_gate is not None and updated.task_id is not None:
        await _preflight_review_gate(
            review_gate,
            approval_id,
            updated.task_id,
            decided_by=decided_by,
        )


def _log_state_transition_and_metrics(
    approval_id: str,
    *,
    previous_status: ApprovalStatus,
    saved: ApprovalItem,
    approved: bool,
    decided_by_user_id: str,
) -> None:
    """Emit the state-transition log and decision metric after persistence.

    Fires immediately after the persistence write succeeds so a downstream
    notification or resume-signalling failure cannot strand the approval row
    in a decided state without a corresponding transition entry in the audit
    stream.

    Args:
        approval_id: Approval identifier.
        previous_status: Status the approval was in BEFORE this decision.
        saved: The persisted approval item.
        approved: Whether the action was approved.
        decided_by_user_id: Immutable user id for the PII-free log channel.
    """
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


async def _save_decision_and_notify(  # noqa: PLR0913
    app_state: AppState,
    request: Request[object, object, State],
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
    await _run_review_gate_preflight(
        app_state,
        approval_id,
        updated,
        decided_by=decided_by,
    )

    store = require_service(app_state.slice(ApprovalStateSlice).store, "Approval Store")
    saved = await store.save_if_pending(updated)
    if saved is None:
        msg = "Approval is no longer pending (already decided or expired)"
        logger.warning(
            API_APPROVAL_CONFLICT,
            approval_id=approval_id,
            note=msg,
        )
        raise ConflictError(msg)

    _log_state_transition_and_metrics(
        approval_id,
        previous_status=previous_status,
        saved=saved,
        approved=approved,
        decided_by_user_id=decided_by_user_id,
    )

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
