"""Living-documentation REST controller.

Read-only HTTP surface that mirrors the agent-tool / MCP write surface.
Writes happen in-process via the agent tool or MCP handler; this
controller exposes list / get / history / search for the wiki UI.
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
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.types import NotBlankStr
from synthorg.docs_engine.constants import (
    DOCS_HISTORY_DEFAULT_LIMIT,
    DOCS_SEARCH_DEFAULT_LIMIT,
    DOCS_SEARCH_MAX_LIMIT,
)
from synthorg.docs_engine.enums import DocType
from synthorg.docs_engine.models import (
    DocSearchHit,
    DocSummary,
    DocVersion,
    LivingDocument,
)
from synthorg.docs_engine.service import DocsService
from synthorg.docs_engine.state import DocsStateSlice
from synthorg.observability import get_logger

logger = get_logger(__name__)

_DEFAULT_LIST_LIMIT: Final[int] = 50


def _docs_service(state: State) -> DocsService:
    """Resolve the docs service from app state, surfacing 503 if absent.

    Returns:
        ``DocsService`` instance.

    Raises:
        ServiceUnavailableError: Raised on the corresponding failure path.
    """
    svc: DocsService | None = state.app_state.slice(DocsStateSlice).service
    if svc is None:
        msg = "Living-documentation engine is not wired in this deployment"
        raise ServiceUnavailableError(msg)
    return svc


DocTypeFilter = Annotated[
    DocType | None,
    QueryParameter(
        required=False,
        description="Filter by doc_type (closed DocType enum)",
    ),
]

TagFilter = Annotated[
    NotBlankStr | None,
    QueryParameter(
        required=False,
        max_length=QUERY_MAX_LENGTH,
        description="Filter by tag (exact match)",
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
        le=DOCS_SEARCH_MAX_LIMIT,
        description="Maximum hits to return",
    ),
]


class ProjectDocsController(Controller):
    """Read-only endpoints for living documentation."""

    path = "/projects/{project_id:str}/docs"
    tags = ("project_docs",)

    @get(guards=[require_read_access])
    async def list_docs(  # noqa: PLR0913 -- controller endpoint takes query params per filter dimension
        self,
        state: State,
        project_id: PathId,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIST_LIMIT,
        doc_type: DocTypeFilter = None,
        tag: TagFilter = None,
    ) -> PaginatedResponse[DocSummary]:
        """List docs for a project (recency-first).

        Returns:
            ``PaginatedResponse[DocSummary]`` instance.
        """
        secret = cursor_secret_of(state.app_state)
        offset = 0 if cursor is None else decode_cursor(cursor, secret=secret)
        summaries = await _docs_service(state).list_docs(
            project_id=NotBlankStr(project_id),
            doc_type=doc_type,
            tag=tag,
            limit=limit + 1,
            offset=offset,
        )
        meta = encode_countless_seek_meta(
            offset=offset,
            fetched_rows=len(summaries),
            limit=limit,
            secret=secret,
        )
        return PaginatedResponse[DocSummary](data=summaries[:limit], pagination=meta)

    @get("/search", guards=[require_read_access])
    async def search_docs(
        self,
        state: State,
        project_id: PathId,
        q: SearchQuery,
        limit: SearchLimit = DOCS_SEARCH_DEFAULT_LIMIT,
    ) -> Response[ApiResponse[tuple[DocSearchHit, ...]]]:
        """Semantic search across a project's indexed docs.

        Returns:
            Result matching the declared return annotation.
        """
        hits = await _docs_service(state).search(
            project_id=NotBlankStr(project_id),
            query=q,
            limit=limit,
        )
        return Response(
            content=ApiResponse[tuple[DocSearchHit, ...]](data=hits),
            status_code=200,
        )

    @get("/{slug:str}", guards=[require_read_access])
    async def get_doc(
        self,
        state: State,
        project_id: PathId,
        slug: PathId,
    ) -> Response[ApiResponse[LivingDocument]]:
        """Fetch one living doc by slug.

        ``DocNotFoundError`` propagates to the global RFC 9457 handler,
        which maps it to 404 with the ``LIVING_DOC_NOT_FOUND`` code.

        Returns:
            ``Response[ApiResponse[LivingDocument]]`` instance.
        """
        doc = await _docs_service(state).read_doc(
            project_id=NotBlankStr(project_id),
            slug=NotBlankStr(slug),
        )
        return Response(
            content=ApiResponse[LivingDocument](data=doc),
            status_code=200,
        )

    @get("/{slug:str}/history", guards=[require_read_access])
    async def get_history(
        self,
        state: State,
        project_id: PathId,
        slug: PathId,
        limit: CursorLimit = DOCS_HISTORY_DEFAULT_LIMIT,
    ) -> Response[ApiResponse[tuple[DocVersion, ...]]]:
        """Return the git commit history for one doc.

        ``limit`` caps the returned commits (default 50, max 200); the
        previous fixed 50-commit cap was invisible to callers.

        Returns:
            Result matching the declared return annotation.
        """
        versions = await _docs_service(state).history(
            project_id=NotBlankStr(project_id),
            slug=NotBlankStr(slug),
            limit=limit,
        )
        return Response(
            content=ApiResponse[tuple[DocVersion, ...]](data=versions),
            status_code=200,
        )
