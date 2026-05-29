# module-kind: controller
"""Approvals query endpoints -- list + get with urgency enrichment."""

from datetime import UTC, datetime
from typing import Annotated, Final

from litestar import Controller, get
from litestar.datastructures import State
from litestar.params import QueryParameter

from synthorg._core.features import require_service
from synthorg.api.controllers.approvals._shared import (
    ApprovalResponse,
    _get_approval_or_404,
    _resolve_urgency_thresholds,
    _to_approval_response,
)
from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import require_read_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    paginate_cursor,
)
from synthorg.api.path_params import QUERY_MAX_LENGTH, PathId
from synthorg.api.state import AppState
from synthorg.approval.state import ApprovalStateSlice
from synthorg.core.enums import (
    ApprovalRiskLevel,
    ApprovalStatus,
)

_DEFAULT_LIMIT: Final[int] = 50


class ApprovalsQueryController(Controller):
    """Human approval queue -- read-only list + get."""

    path = "/approvals"
    tags = ("approvals",)

    @get(guards=[require_read_access])
    async def list_approvals(  # noqa: PLR0913
        self,
        state: State,
        status: Annotated[
            ApprovalStatus | None,
            QueryParameter(description="Filter to approvals in this status."),
        ] = None,
        risk_level: Annotated[
            ApprovalRiskLevel | None,
            QueryParameter(description="Filter to approvals at this risk level."),
        ] = None,
        action_type: Annotated[
            str | None,
            QueryParameter(
                max_length=QUERY_MAX_LENGTH,
                description="Filter to approvals raised for this action type.",
            ),
        ] = None,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
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
        app_state: AppState = state.app_state
        store = require_service(
            app_state.slice(ApprovalStateSlice).store, "Approval Store"
        )
        items = await store.list_items(
            status=status,
            risk_level=risk_level,
            action_type=action_type,
        )
        page, meta = paginate_cursor(
            items,
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(app_state),
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
