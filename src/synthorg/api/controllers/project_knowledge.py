"""Knowledge-substrate REST controller.

Read-only HTTP surface for the dashboard, mirroring the agent-tool / MCP
surface. Ingest / reindex / delete happen in-process via the agent tool
or the admin MCP handlers; this controller exposes list / search / get
for the corpus UI.
"""

from typing import TYPE_CHECKING, Annotated, Final

from litestar import Controller, Response, get
from litestar.datastructures import State  # noqa: TC002
from litestar.params import Parameter

from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import require_read_access
from synthorg.api.pagination import CursorLimit, CursorParam, paginate_cursor
from synthorg.api.path_params import QUERY_MAX_LENGTH, PathId
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.types import NotBlankStr
from synthorg.knowledge.constants import (
    KNOWLEDGE_SEARCH_DEFAULT_LIMIT,
    KNOWLEDGE_SEARCH_MAX_LIMIT,
)
from synthorg.knowledge.models import KnowledgeHit, KnowledgeSource
from synthorg.observability import get_logger

if TYPE_CHECKING:
    from synthorg.knowledge.service import KnowledgeService

logger = get_logger(__name__)

_DEFAULT_LIST_LIMIT: Final[int] = 50


def _knowledge_service(state: State) -> KnowledgeService:
    """Resolve the knowledge service from app state, surfacing 503 if absent."""
    svc: KnowledgeService | None = state.app_state.knowledge_service
    if svc is None:
        msg = "Knowledge substrate is not wired in this deployment"
        raise ServiceUnavailableError(msg)
    return svc


IncludeGlobal = Annotated[
    bool,
    Parameter(
        required=False,
        description="Include global (project-unscoped) sources in the listing",
    ),
]

StaleOnly = Annotated[
    bool,
    Parameter(required=False, description="Only sources needing a refresh"),
]

SearchQuery = Annotated[
    NotBlankStr,
    Parameter(required=True, max_length=QUERY_MAX_LENGTH, description="Search text"),
]

SearchLimit = Annotated[
    int,
    Parameter(
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
        """List registered sources for a project (recency-first)."""
        sources = await _knowledge_service(state).list_sources(
            project_id=NotBlankStr(project_id),
            include_global=include_global,
            stale_only=stale_only,
            limit=limit + 1,
        )
        page, meta = paginate_cursor(
            sources,
            limit=limit,
            cursor=cursor,
            secret=state.app_state.cursor_secret,
        )
        return PaginatedResponse[KnowledgeSource](data=page, pagination=meta)

    @get("/search", guards=[require_read_access])
    async def search(
        self,
        state: State,
        project_id: PathId,
        q: SearchQuery,
        limit: SearchLimit = KNOWLEDGE_SEARCH_DEFAULT_LIMIT,
    ) -> Response[ApiResponse[tuple[KnowledgeHit, ...]]]:
        """Cited search across the project + global corpus."""
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
        """
        source = await _knowledge_service(state).get_source(NotBlankStr(source_id))
        return Response(
            content=ApiResponse[KnowledgeSource](data=source),
            status_code=200,
        )
