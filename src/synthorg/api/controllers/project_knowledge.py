"""Knowledge-substrate REST controller.

Read-only HTTP surface for the dashboard, mirroring the agent-tool / MCP
surface. Ingest / reindex / delete happen in-process via the agent tool
or the admin MCP handlers; this controller exposes list / search / get
for the corpus UI.
"""

from typing import TYPE_CHECKING, Annotated, Final

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
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.types import NotBlankStr
from synthorg.knowledge.constants import (
    KNOWLEDGE_SEARCH_DEFAULT_LIMIT,
    KNOWLEDGE_SEARCH_MAX_LIMIT,
)
from synthorg.knowledge.models import KnowledgeHit, KnowledgeSource
from synthorg.knowledge.state import KnowledgeStateSlice
from synthorg.observability import get_logger

if TYPE_CHECKING:
    from synthorg.knowledge.service import KnowledgeService

logger = get_logger(__name__)

_DEFAULT_LIST_LIMIT: Final[int] = 50


def _knowledge_service(state: State) -> KnowledgeService:
    """Resolve the knowledge service from app state, surfacing 503 if absent.

    Returns:
        ``KnowledgeService`` instance.

    Raises:
        ServiceUnavailableError: Raised on the corresponding failure path.
    """
    svc: KnowledgeService | None = state.app_state.slice(KnowledgeStateSlice).service
    if svc is None:
        msg = "Knowledge substrate is not wired in this deployment"
        raise ServiceUnavailableError(msg)
    return svc


IncludeGlobal = Annotated[
    bool,
    QueryParameter(
        required=False,
        description="Include global (project-unscoped) sources in the listing",
    ),
]

StaleOnly = Annotated[
    bool,
    QueryParameter(required=False, description="Only sources needing a refresh"),
]

SearchQuery = Annotated[
    NotBlankStr,
    QueryParameter(
        required=True,
        max_length=QUERY_MAX_LENGTH,
        description="Search text",
    ),
]

SearchLimit = Annotated[
    int,
    QueryParameter(
        required=False,
        ge=1,
        le=KNOWLEDGE_SEARCH_MAX_LIMIT,
        description="Maximum cited hits to return",
    ),
]


class ProjectKnowledgeController(Controller):
    """Read-only endpoints for the knowledge corpus."""

    path = "/projects/{project_id:str}/knowledge"
    tags = ("project_knowledge",)

    @get(guards=[require_read_access])
    async def list_sources(  # noqa: PLR0913 -- endpoint takes a query param per filter dimension
        self,
        state: State,
        project_id: PathId,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIST_LIMIT,
        include_global: IncludeGlobal = True,  # noqa: FBT002 -- HTTP query flag
        stale_only: StaleOnly = False,  # noqa: FBT002 -- HTTP query flag
    ) -> PaginatedResponse[KnowledgeSource]:
        """List registered sources for a project (recency-first).

        Returns:
            ``PaginatedResponse[KnowledgeSource]`` instance.
        """
        secret = cursor_secret_of(state.app_state)
        offset = 0 if cursor is None else decode_cursor(cursor, secret=secret)
        sources = await _knowledge_service(state).list_sources(
            project_id=NotBlankStr(project_id),
            include_global=include_global,
            stale_only=stale_only,
            limit=limit + 1,
            offset=offset,
        )
        meta = encode_countless_seek_meta(
            offset=offset,
            fetched_rows=len(sources),
            limit=limit,
            secret=secret,
        )
        return PaginatedResponse[KnowledgeSource](data=sources[:limit], pagination=meta)

    @get("/search", guards=[require_read_access])
    async def search(
        self,
        state: State,
        project_id: PathId,
        q: SearchQuery,
        limit: SearchLimit = KNOWLEDGE_SEARCH_DEFAULT_LIMIT,
    ) -> Response[ApiResponse[tuple[KnowledgeHit, ...]]]:
        """Cited search across the project + global corpus.

        Returns:
            Result matching the declared return annotation.
        """
        hits = await _knowledge_service(state).search(
            query=q,
            project_id=NotBlankStr(project_id),
            limit=limit,
        )
        return Response(
            content=ApiResponse[tuple[KnowledgeHit, ...]](data=hits),
            status_code=200,
        )

    @get("/{source_id:str}", guards=[require_read_access])
    async def get_source(
        self,
        state: State,
        project_id: PathId,  # noqa: ARG002 -- scopes the route; lookup is by id
        source_id: PathId,
    ) -> Response[ApiResponse[KnowledgeSource]]:
        """Fetch one knowledge source by id.

        ``KnowledgeSourceNotFoundError`` propagates to the global RFC 9457
        handler, which maps it to 404 with ``KNOWLEDGE_SOURCE_NOT_FOUND``.

        Returns:
            ``Response[ApiResponse[KnowledgeSource]]`` instance.
        """
        source = await _knowledge_service(state).get_source(NotBlankStr(source_id))
        return Response(
            content=ApiResponse[KnowledgeSource](data=source),
            status_code=200,
        )


class GlobalKnowledgeController(Controller):
    """Read-only global (project-unscoped) knowledge endpoints.

    Documented in the design spec as the cross-project corpus listing:
    the project-scoped controller above can union global sources via
    ``include_global=true``, but admin UIs and operator tooling that
    only want to see globals (without a project context) read here.
    """

    path = "/knowledge"
    tags = ("knowledge",)

    @get(guards=[require_read_access])
    async def list_global_sources(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIST_LIMIT,
        stale_only: StaleOnly = False,  # noqa: FBT002 -- HTTP query flag
    ) -> PaginatedResponse[KnowledgeSource]:
        """List every global (project-less) source, most-recent first.

        Returns:
            ``PaginatedResponse[KnowledgeSource]`` instance.
        """
        secret = cursor_secret_of(state.app_state)
        offset = 0 if cursor is None else decode_cursor(cursor, secret=secret)
        sources = await _knowledge_service(state).list_sources(
            project_id=None,
            include_global=True,
            stale_only=stale_only,
            limit=limit + 1,
            offset=offset,
        )
        meta = encode_countless_seek_meta(
            offset=offset,
            fetched_rows=len(sources),
            limit=limit,
            secret=secret,
        )
        return PaginatedResponse[KnowledgeSource](data=sources[:limit], pagination=meta)
