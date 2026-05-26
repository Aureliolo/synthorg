"""Subworkflow registry controller -- CRUD + list + find parents.

Exposes the :class:`SubworkflowRegistry` over HTTP at ``/subworkflows``.
Parent workflows are authored through the existing ``/workflows``
controller; this controller is a dedicated surface for the versioned
subworkflow registry.
"""

from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from litestar import Controller, Response, delete, get, post
from litestar.datastructures import State  # noqa: TC002
from litestar.params import PathParameter, QueryParameter
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from synthorg.api.auth import get_authenticated_user_id
from synthorg.api.cursor import InvalidCursorError, decode_keyset_cursor
from synthorg.api.dto import DEFAULT_LIMIT, ApiResponse, PaginatedResponse
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    encode_keyset_meta,
    paginate_cursor,
)
from synthorg.api.path_params import PathId  # noqa: TC001
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.core.enums import WorkflowType
from synthorg.core.types import NotBlankStr
from synthorg.engine.errors import WorkflowDefinitionValidationError
from synthorg.engine.workflow.definition import (
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowIODeclaration,
    WorkflowNode,
)
from synthorg.engine.workflow.subworkflow_models import (
    ParentReference,
    SubworkflowSummary,
)
from synthorg.engine.workflow.subworkflow_registry import (
    SubworkflowRegistry,
    encode_subworkflow_keyset,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_CURSOR_INVALID
from synthorg.persistence._shared import collect_all

logger = get_logger(__name__)


class CreateSubworkflowRequest(BaseModel):
    """Payload for publishing a new subworkflow version.

    Attributes:
        subworkflow_id: Identifier.  Generated server-side when omitted.
        version: Semver string.  Defaults to ``"1.0.0"``.
        name: Human-readable name.
        description: Optional description.
        workflow_type: Target workflow type.
        inputs: Declared input contract.
        outputs: Declared output contract.
        nodes: Graph node payloads.
        edges: Graph edge payloads.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    subworkflow_id: NotBlankStr | None = Field(
        default=None,
        max_length=128,
        description="Stable identifier (generated when omitted)",
    )
    version: NotBlankStr = Field(
        default="1.0.0",
        max_length=64,
        description="Semver version",
    )
    name: NotBlankStr = Field(max_length=256, description="Display name")
    description: str = Field(default="", max_length=4096)
    workflow_type: WorkflowType = Field(
        default=WorkflowType.SEQUENTIAL_PIPELINE,
    )
    inputs: tuple[dict[str, object], ...] = Field(
        default=(),
        max_length=64,
    )
    outputs: tuple[dict[str, object], ...] = Field(
        default=(),
        max_length=64,
    )
    nodes: tuple[dict[str, object], ...] = Field(
        max_length=500,
    )
    edges: tuple[dict[str, object], ...] = Field(
        max_length=1000,
    )


def _registry(state: State) -> SubworkflowRegistry:
    """Build a :class:`SubworkflowRegistry` from the app state.

    Returns:
        ``SubworkflowRegistry`` instance.
    """
    return SubworkflowRegistry(state.app_state.persistence.subworkflows)


class SubworkflowController(Controller):
    """Versioned subworkflow registry controller.

    Parent workflows remain authored through ``/workflows``; this
    controller exposes the subworkflow library as a distinct surface.
    """

    path = "/subworkflows"
    tags = ("subworkflows",)

    @get("", guards=[require_read_access])
    async def list_subworkflows(
        self,
        state: State,
        cursor: CursorParam = None,
        limit: CursorLimit = DEFAULT_LIMIT,
    ) -> PaginatedResponse[SubworkflowSummary]:
        """List subworkflows with keyset-based cursor pagination.

        Sorted by ``(name, latest_version, subworkflow_id)`` -- the
        ``subworkflow_id`` tail is the required tie-breaker so two
        summaries sharing ``(name, latest_version)`` cannot drift
        between pages.  The cursor encodes a JSON-serialised triple
        ``["name", "latest_version", "subworkflow_id"]`` (see
        :func:`encode_subworkflow_keyset`); a naive
        ``f"{name}|{version}|{id}"`` join could collide whenever any
        component contains the delimiter, since ``NotBlankStr`` does
        not forbid pipes / colons / etc.  The next page reads where
        the composite sort key tuple ``(name, latest_version,
        subworkflow_id)`` is strictly greater than the decoded
        triple.  Keyset contract is stable under concurrent inserts
        and deletes.

        Args:
            state: Application state.
            cursor: Opaque keyset cursor from a previous page.
            limit: Page size (default 50, max defined by ``MAX_LIMIT``).

        Returns:
            Paginated response of subworkflow summaries.

        Raises:
            InvalidCursorError: HTTP 400 -- malformed, tampered, or
                signed by a different secret.
        """
        app_state: AppState = state.app_state
        registry = _registry(state)
        try:
            after_key = (
                decode_keyset_cursor(cursor, secret=app_state.cursor_secret)
                if cursor is not None
                else None
            )
        except InvalidCursorError:
            # The cursor is attacker-controlled input and may carry
            # secret fragments from tampering attempts -- log only
            # the failure reason, never the cursor itself.  Mirrors
            # ``paginate_cursor`` in ``api/pagination.py``.
            logger.warning(
                API_CURSOR_INVALID,
                reason="subworkflow_cursor_decode_failed",
            )
            raise
        try:
            page, has_more = await registry.list_page(
                after_key=after_key,
                limit=limit,
            )
        except ValueError as exc:
            # The decoded ``after_key`` is HMAC-signed by the server,
            # but the structural-validity check inside the registry
            # (decode the JSON triple) still raises ``ValueError`` on
            # any tampered / hand-crafted payload that survived the
            # signature step. Surface as HTTP 400 instead of letting
            # it bubble up as a 500 -- the cursor is attacker-
            # controllable input.
            logger.warning(
                API_CURSOR_INVALID,
                reason="subworkflow_keyset_payload_malformed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "subworkflow keyset cursor payload is malformed"
            raise InvalidCursorError(msg) from exc
        # JSON-encode the composite sort key so names containing
        # ``|``/``:``/etc. cannot collide with the cursor delimiter
        # (NotBlankStr does not forbid separator characters).
        next_after_key = (
            encode_subworkflow_keyset(page[-1]) if has_more and page else None
        )
        meta = encode_keyset_meta(
            next_after_key=next_after_key,
            has_more=has_more,
            limit=limit,
            secret=app_state.cursor_secret,
        )
        return PaginatedResponse(data=page, pagination=meta)

    @get("/search", guards=[require_read_access])
    async def search_subworkflows(
        self,
        state: State,
        q: Annotated[
            str,
            QueryParameter(
                required=True,
                min_length=1,
                max_length=128,
                description="Search substring",
            ),
        ],
        limit: CursorLimit = DEFAULT_LIMIT,
        cursor: CursorParam = None,
    ) -> Response[PaginatedResponse[SubworkflowSummary]]:
        """Substring search across name and description (cursor-paginated).

        Applies opaque-cursor pagination at the API boundary over the
        complete match set: the handler drains every bounded repository
        page via ``collect_all`` first (a truncated set would break the
        cursor walk and under-report matches), then slices the
        requested cursor page for the response.

        Returns:
            Result matching the declared return annotation.
        """
        registry = _registry(state)
        # This endpoint applies its own opaque-cursor pagination over
        # the full match set, so drain every bounded repo page; a
        # truncated set would break the cursor walk and under-report
        # matches.
        matches = await collect_all(
            lambda page_limit, offset: registry.search(
                NotBlankStr(q),
                limit=page_limit,
                offset=offset,
            ),
        )
        page, meta = paginate_cursor(
            matches,
            limit=limit,
            cursor=cursor,
            secret=state.app_state.cursor_secret,
        )
        return Response(
            content=PaginatedResponse[SubworkflowSummary](data=page, pagination=meta),
        )

    @get("/{subworkflow_id:str}/versions", guards=[require_read_access])
    async def list_versions(
        self,
        state: State,
        subworkflow_id: PathId,
        limit: CursorLimit = DEFAULT_LIMIT,
        cursor: CursorParam = None,
    ) -> Response[PaginatedResponse[str]]:
        """List every semver for a subworkflow, newest first (cursor-paginated).

        Returns:
            ``Response[PaginatedResponse[str]]`` instance.
        """
        registry = _registry(state)
        versions = await registry.list_versions(subworkflow_id)
        page, meta = paginate_cursor(
            versions,
            limit=limit,
            cursor=cursor,
            secret=state.app_state.cursor_secret,
        )
        return Response(
            content=PaginatedResponse[str](data=page, pagination=meta),
        )

    @get(
        "/{subworkflow_id:str}/versions/{version:str}",
        guards=[require_read_access],
    )
    async def get_version(
        self,
        state: State,
        subworkflow_id: PathId,
        version: Annotated[
            str,
            PathParameter(
                min_length=1,
                max_length=64,
                description="Subworkflow version to fetch (semver-like, e.g. 1.0.0).",
            ),
        ],
    ) -> Response[ApiResponse[WorkflowDefinition]]:
        """Fetch a specific subworkflow version.

        Raises ``SubworkflowNotFoundError`` (404) when the version
        cannot be resolved; the domain-error handler maps it to an
        RFC 9457 response automatically.

        Returns:
            Result matching the declared return annotation.
        """
        registry = _registry(state)
        definition = await registry.get(subworkflow_id, version)
        return Response(
            content=ApiResponse[WorkflowDefinition](data=definition),
        )

    @get(
        "/{subworkflow_id:str}/versions/{version:str}/parents",
        guards=[require_read_access],
    )
    async def list_parents(
        self,
        state: State,
        subworkflow_id: PathId,
        version: Annotated[
            str,
            PathParameter(
                min_length=1,
                max_length=64,
                description="Subworkflow version pinned by the parent workflows.",
            ),
        ],
        limit: CursorLimit = DEFAULT_LIMIT,
        cursor: CursorParam = None,
    ) -> Response[PaginatedResponse[ParentReference]]:
        """List parent workflow definitions pinning this version.

        Applies opaque-cursor pagination at the API boundary over the
        complete parent set: the handler drains every bounded
        repository page via ``collect_all`` first (a truncated set
        would break the cursor walk and under-report references), then
        slices the requested cursor page for the response.

        Returns:
            Result matching the declared return annotation.
        """
        registry = _registry(state)
        # This endpoint applies its own opaque-cursor pagination over
        # the full parent set, so drain every bounded repo page; a
        # truncated set would break the cursor walk and (worse)
        # under-report references.
        parents = await collect_all(
            lambda page_limit, offset: registry.find_parents(
                NotBlankStr(subworkflow_id),
                NotBlankStr(version),
                limit=page_limit,
                offset=offset,
            ),
        )
        page, meta = paginate_cursor(
            parents,
            limit=limit,
            cursor=cursor,
            secret=state.app_state.cursor_secret,
        )
        return Response(
            content=PaginatedResponse[ParentReference](data=page, pagination=meta),
        )

    @post("", guards=[require_write_access])
    async def create_subworkflow(
        self,
        state: State,
        data: CreateSubworkflowRequest,
    ) -> Response[ApiResponse[WorkflowDefinition]]:
        """Publish a new subworkflow version to the registry.

        Returns:
            Result matching the declared return annotation.

        Raises:
            WorkflowDefinitionValidationError: Raised on the corresponding failure path.
        """
        creator = get_authenticated_user_id()
        now = datetime.now(UTC)
        subworkflow_id = data.subworkflow_id or f"sub-{uuid4().hex[:12]}"
        try:
            definition = WorkflowDefinition(
                id=subworkflow_id,
                name=data.name,
                description=data.description,
                workflow_type=data.workflow_type,
                version=data.version,
                inputs=tuple(
                    WorkflowIODeclaration.model_validate(i) for i in data.inputs
                ),
                outputs=tuple(
                    WorkflowIODeclaration.model_validate(o) for o in data.outputs
                ),
                is_subworkflow=True,
                nodes=tuple(WorkflowNode.model_validate(n) for n in data.nodes),
                edges=tuple(WorkflowEdge.model_validate(e) for e in data.edges),
                created_by=creator,
                created_at=now,
                updated_at=now,
            )
        except (ValueError, ValidationError) as exc:
            msg = "Invalid subworkflow definition"
            raise WorkflowDefinitionValidationError(msg) from exc

        registry = _registry(state)
        await registry.register(definition)

        return Response(
            content=ApiResponse[WorkflowDefinition](data=definition),
            status_code=201,
        )

    @delete(
        "/{subworkflow_id:str}/versions/{version:str}",
        guards=[require_write_access],
        status_code=200,
    )
    async def delete_version(
        self,
        state: State,
        subworkflow_id: PathId,
        version: Annotated[
            str,
            PathParameter(
                min_length=1,
                max_length=64,
                description="Subworkflow version to delete (parents block).",
            ),
        ],
    ) -> Response[ApiResponse[None]]:
        """Delete a subworkflow version.

        Returns 409 when any parent workflow still pins the version;
        404 when the coordinate does not exist.

        Returns:
            ``Response[ApiResponse[None]]`` instance.
        """
        registry = _registry(state)
        await registry.delete(subworkflow_id, version)
        return Response(content=ApiResponse[None]())
