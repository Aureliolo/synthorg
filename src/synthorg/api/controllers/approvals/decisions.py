# module-kind: controller
"""Approvals decision endpoints -- create, approve, reject."""

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated, Final
from uuid import UUID, uuid4

from litestar import Controller, Request, post
from litestar.datastructures import State
from litestar.params import HeaderParameter

from synthorg._core.features import require_service
from synthorg.api.api_core_state import idempotency_service_of
from synthorg.api.auth.controller_helpers import require_authenticated_user
from synthorg.api.controllers.approvals._notify import (
    _decided_attribution,
    _publish_approval_event,
    _resolve_decision,
    _save_decision_and_notify,
)
from synthorg.api.controllers.approvals._shared import (
    ApprovalResponse,
    _get_approval_or_404,
    _resolve_urgency_thresholds,
    _to_approval_response,
)
from synthorg.api.dto import (
    ApiResponse,
    ApproveRequest,
    CreateApprovalRequest,
    RejectRequest,
)
from synthorg.api.guards import (
    require_approval_roles,
    require_write_access,
)
from synthorg.api.path_params import PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.api.ws_models import WsEventType
from synthorg.approval.enums import ApprovalStatus
from synthorg.approval.state import ApprovalStateSlice
from synthorg.core.approval import ApprovalItem
from synthorg.core.domain_errors import ConflictError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.api import API_APPROVAL_CREATED
from synthorg.observability.events.idempotency import IDEMPOTENCY_CLAIM_IN_FLIGHT

logger = get_logger(__name__)

# The durable idempotency-key column is bounded at 255 chars. Decision
# endpoints store the composite key ``f"{approval_id}:{idempotency_key}"``
# (a 36-char UUID + a ":" separator = 37 chars of prefix), so the caller's
# raw key must stay within 255 - 37 = 218 chars or the composite would
# overflow the column's CHECK constraint.
_MAX_IDEMPOTENCY_KEY_LEN: Final[int] = 218

_IdempotencyKeyHeader = Annotated[
    NotBlankStr,
    HeaderParameter(
        name="Idempotency-Key",
        description=(
            "RFC-style retry-safe key. Required: an identical key within "
            "24h returns the cached decision response instead of re-running "
            "the decision, so a 5xx-driven client retry cannot double-fire "
            "the notification / resume-signal side effects."
        ),
        required=True,
        min_length=1,
        max_length=_MAX_IDEMPOTENCY_KEY_LEN,
    ),
]


async def _decide_idempotent(
    app_state: AppState,
    *,
    scope: str,
    key: str,
    endpoint: str,
    decide: Callable[[], Awaitable[dict[str, object]]],
) -> ApprovalResponse:
    """Run *decide* under the idempotency guard and re-hydrate the response.

    The callback owns the full decision (re-read, PENDING gate, durable
    write, notify) and returns the JSON-dumped :class:`ApprovalResponse`;
    a repeated key returns the cached dict, which is re-validated here so
    callers always receive a typed response.

    Returns:
        The (fresh or cached) approval response.

    Raises:
        ConflictError: If a concurrent in-flight decision holds the same
            idempotency key.
    """
    outcome = await idempotency_service_of(app_state).run_idempotent(
        scope=scope,
        key=key,
        callback=decide,
    )
    if outcome.timed_out:
        logger.warning(
            IDEMPOTENCY_CLAIM_IN_FLIGHT,
            scope=scope,
            idempotency_key=key,
            endpoint=endpoint,
        )
        msg = "Concurrent in-flight decision with this idempotency key"
        raise ConflictError(msg)
    return ApprovalResponse.model_validate(outcome.result)


class ApprovalsDecisionsController(Controller):
    """Human approval queue -- create, approve, reject."""

    path = "/approvals"
    tags = ("approvals",)

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
        request: Request[object, object, State],
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
        auth_user = require_authenticated_user(request)

        app_state: AppState = state.app_state
        now = datetime.now(UTC)
        approval_id = str(uuid4())

        expires_at = None
        if data.ttl_seconds is not None:
            expires_at = now + timedelta(seconds=data.ttl_seconds)

        item = ApprovalItem(
            id=UUID(approval_id),
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
        store = require_service(
            app_state.slice(ApprovalStateSlice).store, "Approval Store"
        )
        await store.add(item)

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
        request: Request[object, object, State],
        idempotency_key: _IdempotencyKeyHeader,
    ) -> ApiResponse[ApprovalResponse]:
        """Approve a pending approval item.

        The ``decided_by`` field is populated from the authenticated
        user's username. The decision runs under the idempotency guard
        so a retried request with the same ``Idempotency-Key`` returns
        the cached response without re-firing the side effects.

        Args:
            state: Application state.
            approval_id: Approval identifier.
            data: Approval payload with optional comment.
            request: The incoming HTTP request.
            idempotency_key: Required caller-supplied retry token.

        Returns:
            Updated approval response with urgency fields.

        Raises:
            NotFoundError: If the approval is not found.
            ConflictError: If the approval is not in PENDING status, or a
                concurrent decision holds the same idempotency key.
        """
        app_state: AppState = state.app_state

        async def _do_approve() -> dict[str, object]:
            item = await _get_approval_or_404(app_state, approval_id)
            _resolve_decision(request, item, approval_id)
            decided_by, decided_by_user_id = _decided_attribution()
            now = datetime.now(UTC)
            previous_status = item.status
            updated = item.model_copy(
                update={
                    "status": ApprovalStatus.APPROVED,
                    "decided_at": now,
                    "decided_by": decided_by,
                    "decision_reason": data.comment,
                },
            )
            # Pre-resolve urgency thresholds before the durable decision
            # write so a slow settings backend can't strand a committed
            # decision behind a blocked response.
            critical_seconds, high_seconds = await _resolve_urgency_thresholds(
                app_state
            )
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
                decided_by=decided_by,
                decided_by_user_id=decided_by_user_id,
                previous_status=previous_status,
                decision_reason=data.comment,
                ws_event=WsEventType.APPROVAL_APPROVED,
            )
            return _to_approval_response(
                saved,
                now=now,
                urgency_critical_seconds=critical_seconds,
                urgency_high_seconds=high_seconds,
            ).model_dump(mode="json")

        response = await _decide_idempotent(
            app_state,
            scope="approval:approve",
            # Bind the approval id into the key so the same caller token
            # reused against a different approval cannot return this one's
            # cached decision (matches the MCP backup handler's pattern).
            key=f"{approval_id}:{idempotency_key}",
            endpoint="approvals.approve",
            decide=_do_approve,
        )
        return ApiResponse(data=response)

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
        request: Request[object, object, State],
        idempotency_key: _IdempotencyKeyHeader,
    ) -> ApiResponse[ApprovalResponse]:
        """Reject a pending approval item.

        The ``decided_by`` field is populated from the authenticated
        user's username. The decision runs under the idempotency guard
        so a retried request with the same ``Idempotency-Key`` returns
        the cached response without re-firing the side effects.

        Args:
            state: Application state.
            approval_id: Approval identifier.
            data: Rejection payload with mandatory reason.
            request: The incoming HTTP request.
            idempotency_key: Required caller-supplied retry token.

        Returns:
            Updated approval response with urgency fields.

        Raises:
            NotFoundError: If the approval is not found.
            ConflictError: If the approval is not in PENDING status, or a
                concurrent decision holds the same idempotency key.
        """
        app_state: AppState = state.app_state

        async def _do_reject() -> dict[str, object]:
            item = await _get_approval_or_404(app_state, approval_id)
            _resolve_decision(request, item, approval_id)
            decided_by, decided_by_user_id = _decided_attribution()
            now = datetime.now(UTC)
            previous_status = item.status
            updated = item.model_copy(
                update={
                    "status": ApprovalStatus.REJECTED,
                    "decided_at": now,
                    "decided_by": decided_by,
                    "decision_reason": data.reason,
                },
            )
            # Pre-resolve urgency thresholds before the durable decision
            # write so a slow settings backend can't strand a committed
            # decision behind a blocked response.
            critical_seconds, high_seconds = await _resolve_urgency_thresholds(
                app_state
            )
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
                decided_by=decided_by,
                decided_by_user_id=decided_by_user_id,
                previous_status=previous_status,
                decision_reason=data.reason,
                ws_event=WsEventType.APPROVAL_REJECTED,
            )
            return _to_approval_response(
                saved,
                now=now,
                urgency_critical_seconds=critical_seconds,
                urgency_high_seconds=high_seconds,
            ).model_dump(mode="json")

        response = await _decide_idempotent(
            app_state,
            scope="approval:reject",
            # Bind the approval id into the key (see ``approve`` above) so a
            # reused token on a different approval cannot collide.
            key=f"{approval_id}:{idempotency_key}",
            endpoint="approvals.reject",
            decide=_do_reject,
        )
        return ApiResponse(data=response)
