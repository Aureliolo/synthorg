# module-kind: controller
"""Polling fallback controller for interrupt management at /interrupts."""

from typing import Annotated

from litestar import Controller, Request, get, post
from litestar.datastructures import State
from litestar.params import QueryParameter

from synthorg.api.controllers.events._shared import (
    _SESSION_ID_PATTERN,
    InterruptResponse,
    ResumeInterruptRequest,
    _require_auth,
    _require_interrupt_store,
    _resolve_interrupt,
)
from synthorg.api.dto import DEFAULT_LIMIT, ApiResponse, PaginatedResponse
from synthorg.api.guards import require_approval_roles, require_read_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    paginate_cursor,
)
from synthorg.api.path_params import QUERY_MAX_LENGTH, PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.core.types import NotBlankStr


class InterruptController(Controller):
    """Polling fallback for interrupt management."""

    path = "/interrupts"
    tags = ("interrupts",)

    @get(guards=[require_read_access])
    async def list_interrupts(
        self,
        state: State,
        session_id: Annotated[
            NotBlankStr | None,
            QueryParameter(
                max_length=QUERY_MAX_LENGTH,
                pattern=_SESSION_ID_PATTERN,
                description="Filter to interrupts for this session; omit to list all.",
            ),
        ] = None,
        limit: CursorLimit = DEFAULT_LIMIT,
        cursor: CursorParam = None,
    ) -> PaginatedResponse[InterruptResponse]:
        """List pending interrupts (cursor-paginated).

        Args:
            state: Application state.
            session_id: Optional session filter.
            limit: Page size (default 50, max 200).
            cursor: Opaque cursor from the previous page.

        Returns:
            A bounded page of pending interrupts.
        """
        app_state: AppState = state.app_state
        store = _require_interrupt_store(app_state)
        pending = await store.list_pending(session_id=session_id)
        items = tuple(
            InterruptResponse(
                id=i.id,
                type=i.type,
                session_id=i.session_id,
                agent_id=i.agent_id,
                created_at=i.created_at.isoformat(),
                timeout_seconds=i.timeout_seconds,
                tool_name=i.tool_name,
                tool_args=i.tool_args,
                evidence_package_id=i.evidence_package_id,
                question=i.question,
                context_snippet=i.context_snippet,
            )
            for i in pending
        )
        page, meta = paginate_cursor(
            items,
            limit=limit,
            cursor=cursor,
            secret=cursor_secret_of(app_state),
        )
        return PaginatedResponse(data=page, pagination=meta)

    @post(
        "/{interrupt_id:str}/resume",
        guards=[
            require_approval_roles,
            per_op_rate_limit_from_policy("interrupts.resume", key="user"),
        ],
        status_code=200,
    )
    async def resume(
        self,
        state: State,
        interrupt_id: PathId,
        data: ResumeInterruptRequest,
        request: Request[object, object, State],
    ) -> ApiResponse[dict[str, str]]:
        """Resume a pending interrupt via polling API.

        Args:
            state: Application state.
            interrupt_id: Interrupt to resume.
            data: Resume payload.
            request: The incoming HTTP request.

        Returns:
            Confirmation envelope.
        """
        app_state: AppState = state.app_state
        store = _require_interrupt_store(app_state)
        auth_user = _require_auth(request)
        return await _resolve_interrupt(
            store,
            interrupt_id,
            data,
            auth_user.username,
        )
