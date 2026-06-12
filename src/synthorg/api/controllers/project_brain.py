"""Long-horizon project-brain REST controller.

Read-only HTTP surface that mirrors the agent-tool / MCP write surface. Writes
happen in-process via the agent tool or MCP handler; this controller exposes
list / get / history / search for the operator dashboard.
"""

from typing import Annotated, Final

from litestar import Controller, Response, get
from litestar.datastructures import State
from litestar.params import QueryParameter

from synthorg.api.cursor import decode_cursor
from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import require_read_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    cursor_secret_of,
    encode_countless_seek_meta,
)
from synthorg.api.path_params import QUERY_MAX_LENGTH, PathId
from synthorg.core.domain_errors import ServiceUnavailableError, ValidationError
from synthorg.core.types import NotBlankStr
from synthorg.project_brain.constants import (
    BRAIN_SEARCH_DEFAULT_LIMIT,
    BRAIN_SEARCH_MAX_LIMIT,
)
from synthorg.project_brain.models import (
    BrainEntry,
    BrainEntryKind,
    BrainEntryStatus,
    BrainEntryVersion,
    BrainSearchHit,
    BrainSummary,
)
from synthorg.project_brain.service import ProjectBrainService
from synthorg.project_brain.state import ProjectBrainStateSlice

_DEFAULT_LIST_LIMIT: Final[int] = 50


def _brain_service(state: State) -> ProjectBrainService:
    """Resolve the brain service from app state, surfacing 503 if absent.

    Returns:
        The wired :class:`ProjectBrainService`.

    Raises:
        ServiceUnavailableError: When the brain engine is not wired.
    """
    svc: ProjectBrainService | None = state.app_state.slice(
        ProjectBrainStateSlice
    ).service
    if svc is None:
        msg = "Long-horizon project brain is not wired in this deployment"
        raise ServiceUnavailableError(msg)
    return svc


KindFilter = Annotated[
    NotBlankStr | None,
    QueryParameter(
        required=False,
        max_length=QUERY_MAX_LENGTH,
        description=(
            "Filter by entry_kind (decision / open_question / blocker / risk / "
            "dependency / plan_revision)"
        ),
    ),
]

StatusFilter = Annotated[
    NotBlankStr | None,
    QueryParameter(
        required=False,
        max_length=QUERY_MAX_LENGTH,
        description="Filter by status (e.g. open / accepted / blocked / active)",
    ),
]

SearchQuery = Annotated[
    NotBlankStr,
    QueryParameter(
        required=True,
        max_length=QUERY_MAX_LENGTH,
        description="Search query text",
    ),
]

SearchLimit = Annotated[
    int,
    QueryParameter(
        required=False,
        ge=1,
        le=BRAIN_SEARCH_MAX_LIMIT,
        description="Maximum hits to return",
    ),
]


def _parse_kind(value: NotBlankStr | None) -> BrainEntryKind | None:
    """Parse an optional entry-kind filter.

    Returns:
        The parsed kind, or ``None`` when no filter was supplied.

    Raises:
        ValidationError: When ``value`` is not a valid entry kind.
    """
    if value is None:
        return None
    try:
        return BrainEntryKind(value)
    except ValueError as exc:
        valid = ", ".join(k.value for k in BrainEntryKind)
        msg = f"Invalid entry_kind: {value!r}. Valid values: {valid}"
        raise ValidationError(msg) from exc


def _parse_status(value: NotBlankStr | None) -> BrainEntryStatus | None:
    """Parse an optional status filter.

    Returns:
        The parsed status, or ``None`` when no filter was supplied.

    Raises:
        ValidationError: When ``value`` is not a valid status.
    """
    if value is None:
        return None
    try:
        return BrainEntryStatus(value)
    except ValueError as exc:
        valid = ", ".join(s.value for s in BrainEntryStatus)
        msg = f"Invalid status: {value!r}. Valid values: {valid}"
        raise ValidationError(msg) from exc


class ProjectBrainController(Controller):
    """Read-only endpoints for the long-horizon project brain."""

    path = "/projects/{project_id:str}/brain"
    tags = ("project_brain",)

    @get(guards=[require_read_access])
    async def list_entries(  # noqa: PLR0913 -- endpoint takes a query param per filter
        self,
        state: State,
        project_id: PathId,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIST_LIMIT,
        entry_kind: KindFilter = None,
        status: StatusFilter = None,
    ) -> PaginatedResponse[BrainSummary]:
        """List the current-state projection for a project (newest-first).

        Returns:
            A cursor-paginated page of :class:`BrainSummary`.
        """
        secret = cursor_secret_of(state.app_state)
        offset = 0 if cursor is None else decode_cursor(cursor, secret=secret)
        summaries = await _brain_service(state).list_current(
            project_id=NotBlankStr(project_id),
            entry_kind=_parse_kind(entry_kind),
            status=_parse_status(status),
            limit=limit + 1,
            offset=offset,
        )
        meta = encode_countless_seek_meta(
            offset=offset,
            fetched_rows=len(summaries),
            limit=limit,
            secret=secret,
        )
        return PaginatedResponse[BrainSummary](data=summaries[:limit], pagination=meta)

    @get("/search", guards=[require_read_access])
    async def search_entries(
        self,
        state: State,
        project_id: PathId,
        q: SearchQuery,
        limit: SearchLimit = BRAIN_SEARCH_DEFAULT_LIMIT,
    ) -> Response[ApiResponse[tuple[BrainSearchHit, ...]]]:
        """Semantic search across a project's indexed brain entries.

        Returns:
            An :class:`ApiResponse` wrapping the ordered search hits.
        """
        hits = await _brain_service(state).query(
            project_id=NotBlankStr(project_id),
            query=q,
            limit=limit,
        )
        return Response(
            content=ApiResponse[tuple[BrainSearchHit, ...]](data=hits),
            status_code=200,
        )

    @get("/{entry_id:str}", guards=[require_read_access])
    async def get_entry(
        self,
        state: State,
        project_id: PathId,
        entry_id: PathId,
    ) -> Response[ApiResponse[BrainEntry]]:
        """Fetch the latest revision of one brain entry.

        ``BrainEntryNotFoundError`` propagates to the global RFC 9457 handler,
        which maps it to 404 with the ``BRAIN_ENTRY_NOT_FOUND`` code.

        Returns:
            An :class:`ApiResponse` wrapping the latest :class:`BrainEntry`.
        """
        entry = await _brain_service(state).get_entry(
            project_id=NotBlankStr(project_id),
            entry_id=NotBlankStr(entry_id),
        )
        return Response(
            content=ApiResponse[BrainEntry](data=entry),
            status_code=200,
        )

    @get("/{entry_id:str}/history", guards=[require_read_access])
    async def get_history(
        self,
        state: State,
        project_id: PathId,
        entry_id: PathId,
    ) -> Response[ApiResponse[tuple[BrainEntryVersion, ...]]]:
        """Return the git-versioned snapshot history for one entry.

        Returns:
            An :class:`ApiResponse` wrapping the entry's git versions,
            newest-first.
        """
        versions = await _brain_service(state).git_history(
            project_id=NotBlankStr(project_id),
            entry_id=NotBlankStr(entry_id),
        )
        return Response(
            content=ApiResponse[tuple[BrainEntryVersion, ...]](data=versions),
            status_code=200,
        )
