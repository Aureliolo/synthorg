"""Approvals controller -- human approval queue CRUD."""

import asyncio
import math
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Any
from uuid import uuid4

from litestar import Controller, Request, get, post
from litestar.channels import ChannelsPlugin  # noqa: TC002
from litestar.datastructures import State  # noqa: TC002
from litestar.params import Parameter
from pydantic import ConfigDict, Field

from synthorg.api.auth.models import AuthenticatedUser
from synthorg.api.channels import CHANNEL_APPROVALS, get_channels_plugin
from synthorg.api.controllers._approval_review_gate import (
    preflight_review_gate,
    signal_resume_intent,
    try_mid_execution_resume,
    try_review_gate_transition,
)
from synthorg.api.dto import (
    ApiResponse,
    ApproveRequest,
    CreateApprovalRequest,
    PaginatedResponse,
    RejectRequest,
)
from synthorg.api.guards import (
    require_approval_roles,
    require_read_access,
    require_write_access,
)
from synthorg.api.pagination import CursorLimit, CursorParam, paginate_cursor
from synthorg.api.path_params import QUERY_MAX_LENGTH, PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.api.ws_models import WsEvent, WsEventType
from synthorg.core.approval import ApprovalItem
from synthorg.core.domain_errors import (
    ConflictError,
    NotFoundError,
    UnauthorizedError,
    ValidationError,
)
from synthorg.core.enums import (
    ApprovalRiskLevel,
    ApprovalStatus,
)
from synthorg.observability import get_logger
from synthorg.observability.events.api import (
    API_APPROVAL_CONFLICT,
    API_APPROVAL_CREATED,
    API_APPROVAL_PUBLISH_FAILED,
    API_RESOURCE_NOT_FOUND,
    API_SETTINGS_BACKEND_RECOVERED,
    API_VALIDATION_FAILED,
)
from synthorg.observability.events.approval_gate import (
    APPROVAL_STATUS_TRANSITIONED,
)
from synthorg.observability.events.security import (
    SECURITY_APPROVAL_APPROVED,
    SECURITY_APPROVAL_REJECTED,
    SECURITY_AUTH_FAILED,
)
from synthorg.settings.enums import SettingNamespace

logger = get_logger(__name__)

_URGENCY_CRITICAL_FALLBACK_SECONDS: float = 3600.0
_URGENCY_HIGH_FALLBACK_SECONDS: float = 14400.0


_urgency_threshold_fallback_logged: bool = False


def _urgency_thresholds_fallback(reason: str) -> tuple[float, float]:
    """Log the fallback warning once and return the registry defaults.

    Idempotent: only the first transition into the fallback state
    emits a log line, so a flapping settings backend doesn't spam.
    """
    global _urgency_threshold_fallback_logged  # noqa: PLW0603
    if not _urgency_threshold_fallback_logged:
        logger.warning(
            API_VALIDATION_FAILED,
            error=reason,
            critical_fallback=_URGENCY_CRITICAL_FALLBACK_SECONDS,
            high_fallback=_URGENCY_HIGH_FALLBACK_SECONDS,
        )
        _urgency_threshold_fallback_logged = True
    return _URGENCY_CRITICAL_FALLBACK_SECONDS, _URGENCY_HIGH_FALLBACK_SECONDS


def _validate_urgency_thresholds(
    critical: float,
    high: float,
) -> tuple[float, float]:
    """Validate resolved thresholds and emit the recovery log on success.

    Thresholds must be non-negative, finite, and ordered
    (``critical < high``); otherwise the urgency bucketing would
    misclassify every approval (a ``critical=high=0`` setting would
    mark everything as ``CRITICAL``).  Invalid values are treated
    identically to a backend outage so the fallback log fires and
    recovery is still possible.
    """
    global _urgency_threshold_fallback_logged  # noqa: PLW0603
    if (
        not (math.isfinite(critical) and math.isfinite(high))
        or critical < 0
        or high < 0
        or critical >= high
    ):
        return _urgency_thresholds_fallback(
            "approval urgency thresholds are invalid"
            " (require 0 <= critical < high, both finite);"
            " using fallback"
        )
    if _urgency_threshold_fallback_logged:
        logger.info(
            API_SETTINGS_BACKEND_RECOVERED,
            setting="approval_urgency_thresholds",
            critical_seconds=critical,
            high_seconds=high,
        )
        _urgency_threshold_fallback_logged = False
    return critical, high


async def _resolve_urgency_thresholds(app_state: AppState) -> tuple[float, float]:
    """Read approval urgency thresholds from the settings backend.

    Falls back to the registry defaults (3600s critical / 14400s high)
    if the settings backend is unavailable.  Per-process log-once so a
    flapping settings backend does not spam the logs.
    """
    if not app_state.has_config_resolver:
        return _urgency_thresholds_fallback(
            "no config resolver available; using approval urgency threshold fallbacks"
        )
    try:
        critical = await app_state.config_resolver.get_float(
            SettingNamespace.API.value, "approval_urgency_critical_seconds"
        )
        high = await app_state.config_resolver.get_float(
            SettingNamespace.API.value, "approval_urgency_high_seconds"
        )
    except asyncio.CancelledError:
        raise
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        return _urgency_thresholds_fallback(
            "failed to resolve approval urgency thresholds;"
            f" using fallback ({type(exc).__name__})"
        )
    return _validate_urgency_thresholds(critical, high)


class UrgencyLevel(StrEnum):
    """How urgently a pending approval needs attention.

    Thresholds: ``critical`` < 1 hour, ``high`` < 4 hours,
    ``normal`` >= 4 hours, ``no_expiry`` when no TTL is set.
    """

    CRITICAL = "critical"
    HIGH = "high"
    NORMAL = "normal"
    NO_EXPIRY = "no_expiry"


class ApprovalResponse(ApprovalItem):
    """Approval item enriched with computed urgency fields.

    Attributes:
        seconds_remaining: Seconds until expiry, clamped to 0.0 for
            expired items (``None`` if no TTL).
        urgency_level: Urgency classification based on time remaining.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    seconds_remaining: float | None = Field(
        ge=0.0,
        description="Seconds until expiry (null if no TTL set)",
    )
    urgency_level: UrgencyLevel = Field(
        description="Urgency classification based on remaining time",
    )


def _to_approval_response(
    item: ApprovalItem,
    *,
    now: datetime,
    urgency_critical_seconds: float,
    urgency_high_seconds: float,
) -> ApprovalResponse:
    """Convert an ApprovalItem to an ApprovalResponse with urgency fields.

    Args:
        item: The domain-layer approval item.
        now: Reference timestamp for computing seconds remaining.
        urgency_critical_seconds: Threshold below which urgency is
            ``CRITICAL`` (resolved per-request from the settings
            backend; falls back to the registry default).
        urgency_high_seconds: Threshold below which urgency is
            ``HIGH``.  Operators must satisfy
            ``urgency_critical_seconds < urgency_high_seconds``; the
            startup invariant validator
            (``lifecycle_helpers._validate_approval_urgency_invariant``)
            blocks bad combinations before traffic arrives.

    Returns:
        Response DTO with computed ``seconds_remaining`` and ``urgency_level``.
    """
    if item.expires_at is None:
        seconds_remaining = None
        urgency = UrgencyLevel.NO_EXPIRY
    else:
        seconds_remaining = max(0.0, (item.expires_at - now).total_seconds())
        # Inclusive comparisons: the settings contract is "at or below"
        # so a TTL exactly at the configured threshold is included in
        # the corresponding bucket (CRITICAL or HIGH) rather than spilling
        # into the next-laxer bucket.
        if seconds_remaining <= urgency_critical_seconds:
            urgency = UrgencyLevel.CRITICAL
        elif seconds_remaining <= urgency_high_seconds:
            urgency = UrgencyLevel.HIGH
        else:
            urgency = UrgencyLevel.NORMAL
    return ApprovalResponse(
        **item.model_dump(),
        seconds_remaining=seconds_remaining,
        urgency_level=urgency,
    )


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
    except MemoryError, RecursionError:
        raise
    except Exception:
        logger.warning(
            API_APPROVAL_PUBLISH_FAILED,
            approval_id=item.id,
            event_type=event_type.value,
            exc_info=True,
        )


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


async def _get_approval_or_404(
    app_state: AppState,
    approval_id: str,
) -> ApprovalItem:
    """Fetch an approval item or raise NotFoundError.

    Args:
        app_state: Application state containing the approval store.
        approval_id: Approval identifier.

    Returns:
        The matching approval item.

    Raises:
        NotFoundError: If the approval is not found.
    """
    item = await app_state.approval_store.get(approval_id)
    if item is None:
        msg = f"Approval {approval_id!r} not found"
        logger.warning(
            API_RESOURCE_NOT_FOUND,
            resource="approval",
            id=approval_id,
        )
        raise NotFoundError(msg)
    return item


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
    review_gate = app_state.review_gate_service
    if review_gate is not None and updated.task_id is not None:
        await _preflight_review_gate(
            review_gate,
            approval_id,
            updated.task_id,
            decided_by=decided_by,
        )

    saved = await app_state.approval_store.save_if_pending(updated)
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
        to_status=updated.status.value,
        decided_by=decided_by_user_id,
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


class ApprovalsController(Controller):
    """Human approval queue -- list, create, approve, reject."""

    path = "/approvals"
    tags = ("approvals",)

    @get(guards=[require_read_access])
    async def list_approvals(  # noqa: PLR0913
        self,
        state: State,
        status: ApprovalStatus | None = None,
        risk_level: ApprovalRiskLevel | None = None,
        action_type: Annotated[str, Parameter(max_length=QUERY_MAX_LENGTH)]
        | None = None,
        cursor: CursorParam = None,
        limit: CursorLimit = 50,
    ) -> PaginatedResponse[ApprovalResponse]:
        """List approval items with optional filters.

        Args:
            state: Application state.
            status: Filter by approval status.
            risk_level: Filter by risk level.
            action_type: Filter by action type string.
            cursor: Opaque pagination cursor from the previous page.
            limit: Page size.

        Returns:
            Paginated approval list with urgency fields.
        """
        # Manual check retained: Litestar Parameter(max_length=...) on
        # query params crashes the worker instead of returning a proper
        # RFC 9457 error response.
        if action_type is not None and len(action_type) > QUERY_MAX_LENGTH:
            msg = f"action_type exceeds maximum length of {QUERY_MAX_LENGTH}"
            logger.warning(
                API_VALIDATION_FAILED,
                field="action_type",
                actual_length=len(action_type),
                max_length=QUERY_MAX_LENGTH,
            )
            raise ValidationError(msg)

        app_state: AppState = state.app_state
        items = await app_state.approval_store.list_items(
            status=status,
            risk_level=risk_level,
            action_type=action_type,
        )
        page, meta = paginate_cursor(
            items,
            limit=limit,
            cursor=cursor,
            secret=app_state.cursor_secret,
        )
        now = datetime.now(UTC)
        critical_seconds, high_seconds = await _resolve_urgency_thresholds(app_state)
        enriched = tuple(
            _to_approval_response(
                i,
                now=now,
                urgency_critical_seconds=critical_seconds,
                urgency_high_seconds=high_seconds,
            )
            for i in page
        )
        return PaginatedResponse(data=enriched, pagination=meta)

    @get("/{approval_id:str}", guards=[require_read_access])
    async def get_approval(
        self,
        state: State,
        approval_id: PathId,
    ) -> ApiResponse[ApprovalResponse]:
        """Get a single approval item by ID.

        Args:
            state: Application state.
            approval_id: Approval identifier.

        Returns:
            Approval response envelope with urgency fields.

        Raises:
            NotFoundError: If the approval is not found.
        """
        app_state: AppState = state.app_state
        item = await _get_approval_or_404(app_state, approval_id)
        critical_seconds, high_seconds = await _resolve_urgency_thresholds(app_state)
        return ApiResponse(
            data=_to_approval_response(
                item,
                now=datetime.now(UTC),
                urgency_critical_seconds=critical_seconds,
                urgency_high_seconds=high_seconds,
            )
        )

    @post(
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("approvals.create", key="user"),
        ],
        status_code=201,
    )
    async def create_approval(
        self,
        state: State,
        data: CreateApprovalRequest,
        request: Request[Any, Any, Any],
    ) -> ApiResponse[ApprovalResponse]:
        """Create a new approval item.

        The ``requested_by`` field is populated from the authenticated
        user's username, not from the request body.

        Args:
            state: Application state.
            data: Approval creation payload.
            request: The incoming HTTP request.

        Returns:
            Created approval item envelope.

        Raises:
            UnauthorizedError: If the user is missing from the request scope.
        """
        auth_user = request.scope.get("user")
        if not isinstance(auth_user, AuthenticatedUser):
            msg = "Authentication required"
            logger.warning(
                SECURITY_AUTH_FAILED,
                endpoint="create_approval",
                note="No authenticated user in request scope",
            )
            raise UnauthorizedError(msg)

        app_state: AppState = state.app_state
        now = datetime.now(UTC)
        approval_id = f"approval-{uuid4().hex}"

        expires_at = None
        if data.ttl_seconds is not None:
            expires_at = now + timedelta(seconds=data.ttl_seconds)

        item = ApprovalItem(
            id=approval_id,
            action_type=data.action_type,
            title=data.title,
            description=data.description,
            requested_by=auth_user.username,
            risk_level=data.risk_level,
            created_at=now,
            expires_at=expires_at,
            task_id=data.task_id,
            metadata=data.metadata,
        )
        # Resolve urgency thresholds BEFORE the durable write so a slow
        # settings backend can't strand a committed approval behind a
        # blocked response (which would prompt the client to retry and
        # double-write the same approval).
        critical_seconds, high_seconds = await _resolve_urgency_thresholds(app_state)
        await app_state.approval_store.add(item)

        _publish_approval_event(
            request,
            WsEventType.APPROVAL_SUBMITTED,
            item,
        )
        logger.info(
            API_APPROVAL_CREATED,
            approval_id=item.id,
            action_type=item.action_type,
            risk_level=item.risk_level.value,
        )
        return ApiResponse(
            data=_to_approval_response(
                item,
                now=now,
                urgency_critical_seconds=critical_seconds,
                urgency_high_seconds=high_seconds,
            )
        )

    @post(
        "/{approval_id:str}/approve",
        guards=[
            require_approval_roles,
            per_op_rate_limit_from_policy("approvals.approve", key="user"),
        ],
        status_code=200,
    )
    async def approve(
        self,
        state: State,
        approval_id: PathId,
        data: ApproveRequest,
        request: Request[Any, Any, Any],
    ) -> ApiResponse[ApprovalResponse]:
        """Approve a pending approval item.

        The ``decided_by`` field is populated from the authenticated
        user's username.

        Args:
            state: Application state.
            approval_id: Approval identifier.
            data: Approval payload with optional comment.
            request: The incoming HTTP request.

        Returns:
            Updated approval response with urgency fields.

        Raises:
            NotFoundError: If the approval is not found.
            ConflictError: If the approval is not in PENDING status.
        """
        app_state: AppState = state.app_state
        item = await _get_approval_or_404(app_state, approval_id)

        auth_user = _resolve_decision(request, item, approval_id)
        now = datetime.now(UTC)
        previous_status = item.status
        updated = item.model_copy(
            update={
                "status": ApprovalStatus.APPROVED,
                "decided_at": now,
                "decided_by": auth_user.username,
                "decision_reason": data.comment,
            },
        )
        # Pre-resolve urgency thresholds before the durable decision
        # write so a slow settings backend can't strand a committed
        # decision behind a blocked response (which would prompt the
        # client to retry against an already-decided approval).
        critical_seconds, high_seconds = await _resolve_urgency_thresholds(app_state)
        # ``_save_decision_and_notify`` emits the
        # ``APPROVAL_STATUS_TRANSITIONED`` log immediately after the
        # persistence write succeeds, so a downstream notification or
        # resume-signal failure cannot strand the row in a decided
        # state without a corresponding transition entry. The log
        # uses ``decided_by_user_id`` (not username) to keep the
        # observability stream free of human-readable identifiers.
        saved = await _save_decision_and_notify(
            app_state,
            request,
            approval_id,
            updated,
            approved=True,
            decided_by=auth_user.username,
            decided_by_user_id=auth_user.user_id,
            previous_status=previous_status,
            decision_reason=data.comment,
            ws_event=WsEventType.APPROVAL_APPROVED,
        )

        return ApiResponse(
            data=_to_approval_response(
                saved,
                now=now,
                urgency_critical_seconds=critical_seconds,
                urgency_high_seconds=high_seconds,
            )
        )

    @post(
        "/{approval_id:str}/reject",
        guards=[
            require_approval_roles,
            per_op_rate_limit_from_policy("approvals.reject", key="user"),
        ],
        status_code=200,
    )
    async def reject(
        self,
        state: State,
        approval_id: PathId,
        data: RejectRequest,
        request: Request[Any, Any, Any],
    ) -> ApiResponse[ApprovalResponse]:
        """Reject a pending approval item.

        The ``decided_by`` field is populated from the authenticated
        user's username.

        Args:
            state: Application state.
            approval_id: Approval identifier.
            data: Rejection payload with mandatory reason.
            request: The incoming HTTP request.

        Returns:
            Updated approval response with urgency fields.

        Raises:
            NotFoundError: If the approval is not found.
            ConflictError: If the approval is not in PENDING status.
        """
        app_state: AppState = state.app_state
        item = await _get_approval_or_404(app_state, approval_id)

        auth_user = _resolve_decision(request, item, approval_id)
        now = datetime.now(UTC)
        previous_status = item.status
        updated = item.model_copy(
            update={
                "status": ApprovalStatus.REJECTED,
                "decided_at": now,
                "decided_by": auth_user.username,
                "decision_reason": data.reason,
            },
        )
        # Pre-resolve urgency thresholds before the durable decision
        # write so a slow settings backend can't strand a committed
        # decision behind a blocked response (which would prompt the
        # client to retry against an already-decided approval).
        critical_seconds, high_seconds = await _resolve_urgency_thresholds(app_state)
        # ``_save_decision_and_notify`` emits the
        # ``APPROVAL_STATUS_TRANSITIONED`` log immediately after the
        # persistence write succeeds (see the approve branch above
        # for the rationale).
        saved = await _save_decision_and_notify(
            app_state,
            request,
            approval_id,
            updated,
            approved=False,
            decided_by=auth_user.username,
            decided_by_user_id=auth_user.user_id,
            previous_status=previous_status,
            decision_reason=data.reason,
            ws_event=WsEventType.APPROVAL_REJECTED,
        )

        return ApiResponse(
            data=_to_approval_response(
                saved,
                now=now,
                urgency_critical_seconds=critical_seconds,
                urgency_high_seconds=high_seconds,
            )
        )
