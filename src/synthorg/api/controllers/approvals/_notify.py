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
from litestar.datastructures import State

from synthorg._core.features import require_service
from synthorg.api.auth.controller_helpers import require_authenticated_user
from synthorg.api.channels import CHANNEL_APPROVALS, get_channels_plugin
from synthorg.api.controllers._approval_review_gate import (
    preflight_review_gate,
    signal_resume_intent,
)
from synthorg.api.controllers.approvals._enrichment import build_approval_response
from synthorg.api.controllers.approvals._shared import (
    ApprovalResponse,
    to_response_without_context,
)
from synthorg.api.resume_intent_outbox import (
    clear_resume_intent,
    record_resume_intent,
)
from synthorg.api.state import AppState
from synthorg.api.ws_models import WsEvent, WsEventType
from synthorg.approval.enums import ApprovalStatus
from synthorg.approval.state import ApprovalStateSlice
from synthorg.approval.task_review import is_task_review
from synthorg.core.actor_context import require_actor
from synthorg.core.approval import ApprovalItem
from synthorg.core.auth.models import AuthenticatedUser
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import ConflictError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import (
    API_APPROVAL_CONFLICT,
    API_APPROVAL_ENRICH_FAILED,
    API_APPROVAL_PUBLISH_FAILED,
)
from synthorg.observability.events.approval_gate import (
    APPROVAL_GATE_RESUME_FAILED,
    APPROVAL_STATUS_TRANSITIONED,
)
from synthorg.observability.events.security import (
    SECURITY_APPROVAL_APPROVED,
    SECURITY_APPROVAL_REJECTED,
)
from synthorg.observability.metrics_hub import record_approval_decision

logger = get_logger(__name__)


async def _publish_approval_event(
    request: Request[object, object, State],
    app_state: AppState,
    event_type: WsEventType,
    item: ApprovalItem,
) -> ApprovalResponse:
    """Publish an enriched approval event and return the enriched response.

    Publishes the full enriched approval under ``payload.approval`` (the
    shape the dashboard's WS handler upserts from), plus ``approval_id`` /
    ``status`` at the top level for cheap envelope routing, so a decision
    or expiry reflects in the queue live instead of waiting for the poll.
    The same enriched response is returned so the HTTP caller reuses it
    instead of resolving the identical context a second time.

    Best-effort and degrading on two independent axes: an enrichment failure
    still publishes the un-enriched response (the queue receives the status
    change rather than dropping the frame), and an unwired channels plugin is a
    logged no-op rather than a raise. Whichever fired, the response built above
    (enriched, or degraded when enrichment already failed) is returned for the
    HTTP body.

    Args:
        request: The incoming HTTP request.
        app_state: Application state (source of the enrichment resolvers).
        event_type: Type of the approval event.
        item: The approval item to include in the payload.

    Returns:
        The enriched :class:`ApprovalResponse` (context-degraded on
        enrichment failure), for the caller to reuse as the HTTP body.
    """
    now = datetime.now(UTC)
    try:
        response = await build_approval_response(app_state, item)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APPROVAL_ENRICH_FAILED,
            approval_id=str(item.id),
            event_type=event_type.value,
            stage="publish",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        response = to_response_without_context(item, now=now)

    channels_plugin = get_channels_plugin(request)
    if channels_plugin is None:
        logger.warning(
            API_APPROVAL_PUBLISH_FAILED,
            approval_id=str(item.id),
            event_type=event_type.value,
            reason="channels_plugin_not_registered",
        )
        return response

    try:
        event = WsEvent(
            event_type=event_type,
            channel=CHANNEL_APPROVALS,
            timestamp=now,
            payload={
                "approval_id": str(item.id),
                "status": item.status.value,
                "approval": response.model_dump(mode="json"),
            },
        )
        channels_plugin.publish(
            event.model_dump_json(),
            channels=[CHANNEL_APPROVALS],
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            API_APPROVAL_PUBLISH_FAILED,
            approval_id=str(item.id),
            event_type=event_type.value,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
    return response


def _decided_attribution() -> tuple[str, str]:
    """Resolve ``(decided_by, decided_by_user_id)`` from the actor seam.

    ADR-0003: the decision attribution comes from the actor bound by
    ``AuthContextMiddleware`` (``label`` == username, ``actor_id`` ==
    immutable user id) rather than the request's auth user, so the audit
    row (display name) and the PII-free observability stream (immutable id)
    draw from a single bound source that cannot drift between them.

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
    separately by ``signal_resume_intent``.
    """
    event = SECURITY_APPROVAL_APPROVED if approved else SECURITY_APPROVAL_REJECTED
    logger.info(
        event,
        approval_id=approval_id,
        decided_by=decided_by,
    )


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
    gate is unwired, the approval has no associated task, or the approval is
    not a review of that task's work.

    That last condition is the discriminator, and it carries the weight. A
    parked question and a plan approval both carry the objective task's id,
    because that is what they are ABOUT, so ``task_id is not None`` alone
    sweeps them into a check written for finished work: the gate then warns
    that a task "reaching review" has no assignee for a task that is not
    reaching review, and an operator whose id matches the objective task's
    assignee has their answer to a question refused as self-review.

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
    if (
        review_gate is not None
        and updated.task_id is not None
        and is_task_review(updated.action_type)
    ):
        await preflight_review_gate(
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
) -> ApprovalResponse:
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
        The enriched approval response for the decided item, built once by
        the WebSocket publish step and reused as the HTTP body so the same
        review context is not resolved a second time.

    Raises:
        ConflictError: If the approval is no longer pending.
        ForbiddenError: If the decider is the original executing agent
            (self-review preflight fails).
        NotFoundError: If the associated task no longer exists.
        Exception: Re-raised unchanged when the resume dispatch fails, after
            the approval has been restored to its pre-decision status so the
            operator can retry immediately.
    """
    await _run_review_gate_preflight(
        app_state,
        approval_id,
        updated,
        decided_by=decided_by,
    )

    store = require_service(app_state.slice(ApprovalStateSlice).store, "Approval Store")
    # The pre-decision row, kept so a failed resume dispatch can put the
    # approval back where an operator can decide it again. Rebuilt from
    # ``updated`` rather than re-read: a re-read could observe a concurrent
    # write, and ``save_if_pending`` below already proves this call is the
    # first writer, so the fields this function decided are the only ones
    # that differ from what was persisted.
    pending_snapshot = updated.model_copy(
        update={
            "status": previous_status,
            "decided_at": None,
            "decided_by": None,
            "decision_reason": None,
        },
    )
    # Recorded BEFORE the decision write: a crash after the decision but
    # before ``signal_resume_intent`` below would otherwise strand the parked
    # task with nothing left PENDING for anyone to act on.
    await record_resume_intent(app_state, approval_id)
    # ``decided_at`` is re-stamped here, AFTER the marker, because the drain
    # retires any marker whose timestamp postdates the decision. The callers
    # build ``updated`` (and its timestamp) before reaching this function, so
    # keeping the ordering here rather than at each call site is what stops a
    # future decision path from silently recording a marker the drain will
    # then throw away.
    updated = updated.model_copy(update={"decided_at": datetime.now(UTC)})
    saved = await store.save_if_pending(updated)
    if saved is None:
        # The marker is left alone: a concurrent winner may own an in-flight
        # resume behind it, and clearing here would delete that safety net.
        # A marker this call recorded and nobody else owns is discarded by
        # the drain's recorded-after-decision check instead.
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

    response = await _publish_approval_event(request, app_state, ws_event, saved)
    _log_approval_decision(
        approval_id,
        approved=approved,
        decided_by=decided_by,
    )
    try:
        await signal_resume_intent(
            app_state,
            approval_id,
            approved=approved,
            decided_by=decided_by,
            decision_reason=decision_reason,
            task_id=saved.task_id,
        )
    except Exception as exc:
        reraise_critical(exc)
        # Mirrors ApprovalResumeDispatcher.resume(): the decision landed but
        # the dispatch did not, so without a rollback the approval is stuck
        # decided. Every dashboard retry would then hit ConflictError until
        # the next process restart's drain picked the marker up, stranding an
        # operator's decision on a transient downstream failure. Restoring it
        # to PENDING makes the retry available immediately.
        await store.save(pending_snapshot)
        await clear_resume_intent(app_state, approval_id)
        logger.warning(
            APPROVAL_GATE_RESUME_FAILED,
            approval_id=approval_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise
    await clear_resume_intent(app_state, approval_id)
    return response
