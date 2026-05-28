"""Project charter controller: deep CEO interview + charter lifecycle.

Exposes the structured requirements-elicitation interview and the
review / edit / approve / cancel lifecycle for the :class:`ProjectCharter`
artifact. On approval the charter drives a real project run through the
work pipeline spine (see :class:`CharterDispatcher`).

All endpoints surface 503 when the charter subsystem is not wired
(``meta.charter.interview_enabled`` off, no LLM provider, or persistence
unavailable). Approve additionally needs the work pipeline + cost
forecast store; it 503s when the dispatcher is absent.
"""

from typing import TYPE_CHECKING, Annotated

from litestar import Controller, get, patch, post
from litestar.datastructures import State
from litestar.params import QueryParameter
from pydantic import BaseModel, ConfigDict, Field

from synthorg.api.cursor import decode_cursor
from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import (
    require_approval_roles,
    require_org_mutation,
    require_read_access,
)
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    encode_countless_seek_meta,
)
from synthorg.api.path_params import PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.core.actor_context import require_actor
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.enums import CharterStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted
from synthorg.meta.charter.models import (
    BudgetEnvelope,
    CharterApprovalResult,
    CharterEditArgs,
    InterviewTurnArgs,
    InterviewTurnResult,
    ProjectCharter,
    ScopeBoundaries,
)
from synthorg.meta.charter.state import CharterStateSlice
from synthorg.observability import get_logger
from synthorg.observability.events.charter import CHARTER_SUBSTRATE_UNAVAILABLE

if TYPE_CHECKING:
    from synthorg.api.state import AppState
    from synthorg.meta.charter.service import CharterInterviewService

logger = get_logger(__name__)

_DEFAULT_PAGE_SIZE: int = 50


class InterviewTurnRequest(BaseModel):
    """Request body for one charter-interview turn."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    message: NotBlankStr = Field(max_length=4000)
    conversation_id: NotBlankStr | None = Field(default=None)
    project: NotBlankStr | None = Field(default=None)


class CharterEditRequest(BaseModel):
    """Request body for an in-place charter edit (DRAFTED only)."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    title: NotBlankStr | None = Field(default=None)
    brief: NotBlankStr | None = Field(default=None)
    goals: tuple[NotBlankStr, ...] | None = Field(default=None)
    constraints: tuple[NotBlankStr, ...] | None = Field(default=None)
    success_criteria: tuple[NotBlankStr, ...] | None = Field(default=None)
    scope: ScopeBoundaries | None = Field(default=None)
    envelope: BudgetEnvelope | None = Field(default=None)


class _DecisionRequest(BaseModel):
    """Empty request body for approve / cancel (actor identity is implicit)."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")


class CharterController(Controller):
    """Deep CEO interview to project charter API endpoints."""

    path = "/meta/charters"
    tags = ["charter"]  # noqa: RUF012
    guards = [require_read_access]  # noqa: RUF012

    def _service(self, state: State) -> CharterInterviewService:
        """Return the charter interview service or raise 503.

        Returns:
            ``CharterInterviewService`` instance.

        Raises:
            ServiceUnavailableError: Raised on the corresponding failure path.
        """
        app_state: AppState = state.app_state
        service = app_state.slice(CharterStateSlice).interview_service
        if service is None:
            logger.warning(
                CHARTER_SUBSTRATE_UNAVAILABLE,
                dependency="charter_service",
                hint=(
                    "Set meta.charter.interview_enabled, register an LLM "
                    "provider, and connect a persistence backend."
                ),
            )
            msg = (
                "Charter interview is not configured. Enable "
                "``meta.charter.interview_enabled``, register an LLM "
                "provider, and connect persistence."
            )
            raise ServiceUnavailableError(msg)
        return service

    @post(
        "/interview",
        status_code=200,
        guards=[
            require_org_mutation(),
            per_op_rate_limit_from_policy("meta.charters.interview", key="user"),
        ],
    )
    async def interview(
        self,
        data: InterviewTurnRequest,
        state: State,
    ) -> ApiResponse[InterviewTurnResult]:
        """Run one interview turn: a question, or a drafted charter.

        Returns 200 with either the next elicitation question (the
        conversation stays open) or the drafted charter for review.

        Returns:
            ``ApiResponse[InterviewTurnResult]`` instance.
        """
        service = self._service(state)
        actor = require_actor()
        # Fence the human-supplied message in a ``<task-data>`` envelope
        # so the model treats it as data, not instructions.
        result = await service.run_turn(
            InterviewTurnArgs(
                message=NotBlankStr(wrap_untrusted(TAG_TASK_DATA, data.message)),
                created_by=NotBlankStr(actor.actor_id),
                conversation_id=data.conversation_id,
                project=data.project,
            )
        )
        return ApiResponse[InterviewTurnResult](data=result)

    @get("/")
    async def list_charters(
        self,
        state: State,
        status: Annotated[CharterStatus | None, QueryParameter()] = None,
        project_id: Annotated[str | None, QueryParameter()] = None,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_PAGE_SIZE,
    ) -> PaginatedResponse[ProjectCharter]:
        """List charters, newest-first, with optional filters.

        Uses opaque cursor-based pagination (see ``web/CLAUDE.md`` and
        the helpers in ``synthorg.api.pagination``): the request takes
        an opaque ``cursor`` string + ``limit``, the response carries
        ``data`` plus ``PaginationMeta`` (``next_cursor`` / ``has_more``)
        so callers walk the catalogue without offset arithmetic.

        Returns:
            ``PaginatedResponse[ProjectCharter]`` instance.
        """
        service = self._service(state)
        actor = require_actor()
        app_state = state.app_state
        # ``decode_cursor`` raises ``InvalidCursorError`` (mapped to 400)
        # for malformed / tampered / foreign-secret cursors; let it
        # bubble so the boundary handler returns the typed error envelope.
        offset = (
            0
            if cursor is None
            else decode_cursor(cursor, secret=cursor_secret_of(app_state))
        )
        # Fetch limit+1 so the overflow row drives ``has_more`` without
        # a separate COUNT(*) round-trip on the repo.
        fetched = await service.list_charters(
            status=status,
            project_id=NotBlankStr(project_id) if project_id else None,
            created_by=NotBlankStr(actor.actor_id),
            limit=limit + 1,
            offset=offset,
        )
        page = fetched[:limit]
        meta = encode_countless_seek_meta(
            offset=offset,
            fetched_rows=len(fetched),
            limit=limit,
            secret=cursor_secret_of(app_state),
        )
        return PaginatedResponse[ProjectCharter](data=page, pagination=meta)

    @get("/{charter_id:str}")
    async def get_charter(
        self,
        charter_id: PathId,
        state: State,
    ) -> ApiResponse[ProjectCharter]:
        """Fetch a single charter by id (creator-only).

        Returns:
            ``ApiResponse[ProjectCharter]`` instance.
        """
        service = self._service(state)
        actor = require_actor()
        charter = await service.get(
            NotBlankStr(charter_id),
            requested_by=NotBlankStr(actor.actor_id),
        )
        return ApiResponse[ProjectCharter](data=charter)

    @patch(
        "/{charter_id:str}",
        guards=[
            require_org_mutation(),
            per_op_rate_limit_from_policy("meta.charters.edit", key="user"),
        ],
    )
    async def edit_charter(
        self,
        charter_id: PathId,
        data: CharterEditRequest,
        state: State,
    ) -> ApiResponse[ProjectCharter]:
        """Apply an in-place edit to a DRAFTED charter.

        Returns:
            ``ApiResponse[ProjectCharter]`` instance.
        """
        service = self._service(state)
        actor = require_actor()
        updated = await service.edit_charter(
            NotBlankStr(charter_id),
            CharterEditArgs(
                title=data.title,
                brief=data.brief,
                goals=data.goals,
                constraints=data.constraints,
                success_criteria=data.success_criteria,
                scope=data.scope,
                envelope=data.envelope,
            ),
            edited_by=NotBlankStr(actor.actor_id),
        )
        return ApiResponse[ProjectCharter](data=updated)

    @post(
        "/{charter_id:str}/approve",
        status_code=200,
        guards=[
            # Approve dispatches the charter to the spine and is gated to
            # CEO / Manager / Board Member, matching the MCP handler's
            # admin guardrail; budget is actually spent here.
            require_approval_roles,
            require_org_mutation(),
            per_op_rate_limit_from_policy("meta.charters.approve", key="user"),
        ],
    )
    async def approve_charter(
        self,
        charter_id: PathId,
        data: _DecisionRequest,
        state: State,
    ) -> ApiResponse[CharterApprovalResult]:
        """Approve a charter and dispatch its project run to the spine.

        Returns:
            ``ApiResponse[CharterApprovalResult]`` instance.

        Raises:
            ServiceUnavailableError: Raised on the corresponding failure path.
        """
        del data
        app_state = state.app_state
        dispatcher = app_state.slice(CharterStateSlice).dispatcher
        if dispatcher is None:
            logger.warning(
                CHARTER_SUBSTRATE_UNAVAILABLE,
                dependency="charter_dispatcher",
                hint="A provider-backed runtime + cost forecast store are required.",
            )
            msg = (
                "Charter approval is not configured; the work pipeline or "
                "cost forecast store is unavailable, so an approved charter "
                "could never run."
            )
            raise ServiceUnavailableError(msg)
        actor = require_actor()
        result = await dispatcher.approve(
            NotBlankStr(charter_id),
            approved_by=NotBlankStr(actor.actor_id),
        )
        return ApiResponse[CharterApprovalResult](data=result)

    @post(
        "/{charter_id:str}/cancel",
        status_code=200,
        guards=[
            require_org_mutation(),
            per_op_rate_limit_from_policy("meta.charters.cancel", key="user"),
        ],
    )
    async def cancel_charter(
        self,
        charter_id: PathId,
        data: _DecisionRequest,
        state: State,
    ) -> ApiResponse[ProjectCharter]:
        """Cancel a DRAFTED charter (terminal).

        Returns:
            ``ApiResponse[ProjectCharter]`` instance.
        """
        del data
        service = self._service(state)
        actor = require_actor()
        cancelled = await service.cancel_charter(
            NotBlankStr(charter_id),
            cancelled_by=NotBlankStr(actor.actor_id),
        )
        return ApiResponse[ProjectCharter](data=cancelled)


__all__ = ["CharterController"]
