"""Approvals controller — human approval queue CRUD."""

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from litestar import Controller, Request, get, post
from litestar.channels import ChannelsPlugin
from litestar.datastructures import State  # noqa: TC002

from ai_company.api.auth.models import AuthenticatedUser
from ai_company.api.channels import CHANNEL_APPROVALS
from ai_company.api.dto import (
    ApiResponse,
    ApproveRequest,
    CreateApprovalRequest,
    PaginatedResponse,
    RejectRequest,
)
from ai_company.api.errors import ConflictError, NotFoundError, UnauthorizedError
from ai_company.api.guards import require_read_access, require_write_access
from ai_company.api.pagination import PaginationLimit, PaginationOffset, paginate
from ai_company.api.state import AppState  # noqa: TC001
from ai_company.api.ws_models import WsEvent, WsEventType
from ai_company.core.approval import ApprovalItem
from ai_company.core.enums import (
    ApprovalRiskLevel,
    ApprovalStatus,
)
from ai_company.observability import get_logger
from ai_company.observability.events.api import (
    API_APPROVAL_APPROVED,
    API_APPROVAL_CONFLICT,
    API_APPROVAL_CREATED,
    API_APPROVAL_PUBLISH_FAILED,
    API_APPROVAL_REJECTED,
    API_AUTH_FAILED,
    API_RESOURCE_NOT_FOUND,
)
from ai_company.observability.events.approval_gate import (
    APPROVAL_GATE_RESUME_TRIGGERED,
)

logger = get_logger(__name__)


def _get_channels_plugin(
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
    for plugin in request.app.plugins:
        if isinstance(plugin, ChannelsPlugin):
            return plugin
    msg = "ChannelsPlugin not registered"
    logger.error(API_APPROVAL_PUBLISH_FAILED, error=msg)
    raise RuntimeError(msg)


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
        channels_plugin = _get_channels_plugin(request)
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
            API_AUTH_FAILED,
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

    Context resumption is not handled by the approval controller.
    A future scheduling component will observe status changes and
    call ``ApprovalGate.resume_context()`` to resume the parked agent.
    """
    event = API_APPROVAL_APPROVED if approved else API_APPROVAL_REJECTED
    logger.info(
        event,
        approval_id=approval_id,
        decided_by=decided_by,
    )


async def _signal_resume_intent(
    app_state: AppState,
    approval_id: str,
    *,
    approved: bool,
    decided_by: str,
    decision_reason: str | None = None,
) -> None:
    """Log that a decision was made so a scheduler can resume the agent.

    This is intentionally a **signalling-only stub**.  It does NOT call
    ``ApprovalGate.resume_context()`` or re-enqueue the parked agent —
    that is the responsibility of a future scheduling component that
    will observe status changes (via log events or store polling) and
    perform the actual resume.

    .. todo:: Wire to a real scheduler once one exists (see §12.4).

    Args:
        app_state: Application state containing the approval gate.
        approval_id: The approval item identifier.
        approved: Whether the action was approved.
        decided_by: Who made the decision.
        decision_reason: Optional reason for the decision.
    """
    approval_gate = app_state.approval_gate
    if approval_gate is None:
        return

    logger.info(
        APPROVAL_GATE_RESUME_TRIGGERED,
        approval_id=approval_id,
        approved=approved,
        decided_by=decided_by,
        has_reason=decision_reason is not None,
    )


class ApprovalsController(Controller):
    """Human approval queue — list, create, approve, reject."""

    path = "/approvals"
    tags = ("approvals",)

    @get(guards=[require_read_access])
    async def list_approvals(  # noqa: PLR0913
        self,
        state: State,
        status: ApprovalStatus | None = None,
        risk_level: ApprovalRiskLevel | None = None,
        action_type: str | None = None,
        offset: PaginationOffset = 0,
        limit: PaginationLimit = 50,
    ) -> PaginatedResponse[ApprovalItem]:
        """List approval items with optional filters.

        Args:
            state: Application state.
            status: Filter by approval status.
            risk_level: Filter by risk level.
            action_type: Filter by action type string.
            offset: Pagination offset.
            limit: Page size.

        Returns:
            Paginated approval list.
        """
        app_state: AppState = state.app_state
        items = await app_state.approval_store.list_items(
            status=status,
            risk_level=risk_level,
            action_type=action_type,
        )
        page, meta = paginate(items, offset=offset, limit=limit)
        return PaginatedResponse(data=page, pagination=meta)

    @get("/{approval_id:str}", guards=[require_read_access])
    async def get_approval(
        self,
        state: State,
        approval_id: str,
    ) -> ApiResponse[ApprovalItem]:
        """Get a single approval item by ID.

        Args:
            state: Application state.
            approval_id: Approval identifier.

        Returns:
            Approval item envelope.

        Raises:
            NotFoundError: If the approval is not found.
        """
        app_state: AppState = state.app_state
        item = await app_state.approval_store.get(approval_id)
        if item is None:
            msg = f"Approval {approval_id!r} not found"
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="approval",
                id=approval_id,
            )
            raise NotFoundError(msg)
        return ApiResponse(data=item)

    @post(guards=[require_write_access], status_code=201)
    async def create_approval(
        self,
        state: State,
        data: CreateApprovalRequest,
        request: Request[Any, Any, Any],
    ) -> ApiResponse[ApprovalItem]:
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
                API_AUTH_FAILED,
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
        return ApiResponse(data=item)

    @post(
        "/{approval_id:str}/approve",
        guards=[require_write_access],
        status_code=200,
    )
    async def approve(
        self,
        state: State,
        approval_id: str,
        data: ApproveRequest,
        request: Request[Any, Any, Any],
    ) -> ApiResponse[ApprovalItem]:
        """Approve a pending approval item.

        The ``decided_by`` field is populated from the authenticated
        user's username.

        Args:
            state: Application state.
            approval_id: Approval identifier.
            data: Approval payload with optional comment.
            request: The incoming HTTP request.

        Returns:
            Updated approval item envelope.

        Raises:
            NotFoundError: If the approval is not found.
            ConflictError: If the approval is not in PENDING status.
        """
        app_state: AppState = state.app_state
        item = await app_state.approval_store.get(approval_id)
        if item is None:
            msg = f"Approval {approval_id!r} not found"
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="approval",
                id=approval_id,
            )
            raise NotFoundError(msg)

        auth_user = _resolve_decision(request, item, approval_id)
        now = datetime.now(UTC)
        updated = item.model_copy(
            update={
                "status": ApprovalStatus.APPROVED,
                "decided_at": now,
                "decided_by": auth_user.username,
                "decision_reason": data.comment,
            },
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

        _publish_approval_event(
            request,
            WsEventType.APPROVAL_APPROVED,
            updated,
        )
        _log_approval_decision(
            approval_id,
            approved=True,
            decided_by=auth_user.username,
        )
        await _signal_resume_intent(
            app_state,
            approval_id,
            approved=True,
            decided_by=auth_user.username,
            decision_reason=data.comment,
        )

        return ApiResponse(data=saved)

    @post(
        "/{approval_id:str}/reject",
        guards=[require_write_access],
        status_code=200,
    )
    async def reject(
        self,
        state: State,
        approval_id: str,
        data: RejectRequest,
        request: Request[Any, Any, Any],
    ) -> ApiResponse[ApprovalItem]:
        """Reject a pending approval item.

        The ``decided_by`` field is populated from the authenticated
        user's username.

        Args:
            state: Application state.
            approval_id: Approval identifier.
            data: Rejection payload with mandatory reason.
            request: The incoming HTTP request.

        Returns:
            Updated approval item envelope.

        Raises:
            NotFoundError: If the approval is not found.
            ConflictError: If the approval is not in PENDING status.
        """
        app_state: AppState = state.app_state
        item = await app_state.approval_store.get(approval_id)
        if item is None:
            msg = f"Approval {approval_id!r} not found"
            logger.warning(
                API_RESOURCE_NOT_FOUND,
                resource="approval",
                id=approval_id,
            )
            raise NotFoundError(msg)

        auth_user = _resolve_decision(request, item, approval_id)
        now = datetime.now(UTC)
        updated = item.model_copy(
            update={
                "status": ApprovalStatus.REJECTED,
                "decided_at": now,
                "decided_by": auth_user.username,
                "decision_reason": data.reason,
            },
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

        _publish_approval_event(
            request,
            WsEventType.APPROVAL_REJECTED,
            updated,
        )
        _log_approval_decision(
            approval_id,
            approved=False,
            decided_by=auth_user.username,
        )
        await _signal_resume_intent(
            app_state,
            approval_id,
            approved=False,
            decided_by=auth_user.username,
            decision_reason=data.reason,
        )

        return ApiResponse(data=saved)
