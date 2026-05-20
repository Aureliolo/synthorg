"""Living-documentation REST controller.

Read-only HTTP surface that mirrors the agent-tool / MCP write surface.
Writes happen in-process via the agent tool or MCP handler; this
controller exposes list / get / history / search for the wiki UI.
"""

from typing import TYPE_CHECKING, Annotated, Final

from litestar import Controller, Response, get
from litestar.datastructures import State  # noqa: TC002
from litestar.params import Parameter

from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import require_read_access
from synthorg.api.pagination import CursorLimit, CursorParam, paginate_cursor
from synthorg.api.path_params import QUERY_MAX_LENGTH, PathId
from synthorg.core.domain_errors import NotFoundError, ValidationError
from synthorg.core.enums import DocType
from synthorg.core.types import NotBlankStr
from synthorg.docs_engine.constants import (
    DOCS_SEARCH_DEFAULT_LIMIT,
    DOCS_SEARCH_MAX_LIMIT,
)
from synthorg.docs_engine.errors import DocNotFoundError
from synthorg.docs_engine.models import (
    DocSearchHit,
    DocSummary,
    DocVersion,
    LivingDocument,
)
from synthorg.observability import get_logger

if TYPE_CHECKING:
    from synthorg.docs_engine.service import DocsService

logger = get_logger(__name__)

_DEFAULT_LIST_LIMIT: Final[int] = 50


def _docs_service(state: State) -> DocsService:
    """Resolve the docs service from app state, surfacing 503 if absent."""
    svc: DocsService | None = state.app_state.docs_service
    if svc is None:
        msg = "Living-documentation engine is not wired in this deployment"
        raise NotFoundError(msg)
    return svc


DocTypeFilter = Annotated[
    NotBlankStr | None,
    Parameter(
        required=False,
        max_length=QUERY_MAX_LENGTH,
        description="Filter by doc_type (status_report / deliverable / knowledge_note)",
    ),
]

TagFilter = Annotated[
    NotBlankStr | None,
    Parameter(
        required=False,
        max_length=QUERY_MAX_LENGTH,
        description="Filter by tag (exact match)",
    ),
]

SearchQuery = Annotated[
    NotBlankStr,
    Parameter(
        required=True,
        max_length=QUERY_MAX_LENGTH,
        description="Search query text",
    ),
]

SearchLimit = Annotated[
    int,
    Parameter(
        required=False,
        ge=1,
        le=DOCS_SEARCH_MAX_LIMIT,
        description="Maximum hits to return",
    ),
]


def _parse_doc_type(value: NotBlankStr | None) -> DocType | None:
    if value is None:
        return None
    try:
        return DocType(value)
    except ValueError as exc:
        valid = ", ".join(t.value for t in DocType)
        msg = f"Invalid doc_type: {value!r}. Valid values: {valid}"
        raise ValidationError(msg) from exc


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
        """List docs for a project (recency-first)."""
        parsed = _parse_doc_type(doc_type)
        summaries = await _docs_service(state).list_docs(
            project_id=NotBlankStr(project_id),
            doc_type=parsed,
            tag=tag,
            limit=limit + 1,
        )
        page, meta = paginate_cursor(
            summaries,
            limit=limit,
            cursor=cursor,
            secret=state.app_state.cursor_secret,
        )
        return PaginatedResponse[DocSummary](data=page, pagination=meta)

    @get("/search", guards=[require_read_access])
    async def search_docs(
        self,
        state: State,
        project_id: PathId,
        q: SearchQuery,
        limit: SearchLimit = DOCS_SEARCH_DEFAULT_LIMIT,
    ) -> Response[ApiResponse[tuple[DocSearchHit, ...]]]:
        """Semantic search across a project's indexed docs."""
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
        """Fetch one living doc by slug."""
        try:
            doc = await _docs_service(state).read_doc(
                project_id=NotBlankStr(project_id),
                slug=NotBlankStr(slug),
            )
        except DocNotFoundError as exc:
            msg = f"Living doc {project_id!r}/{slug!r} not found"
            raise NotFoundError(msg) from exc
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
    ) -> Response[ApiResponse[tuple[DocVersion, ...]]]:
        """Return the git commit history for one doc."""
        try:
            versions = await _docs_service(state).history(
                project_id=NotBlankStr(project_id),
                slug=NotBlankStr(slug),
            )
        except DocNotFoundError as exc:
            msg = f"Living doc {project_id!r}/{slug!r} not found"
            raise NotFoundError(msg) from exc
        return Response(
            content=ApiResponse[tuple[DocVersion, ...]](data=versions),
            status_code=200,
        )
