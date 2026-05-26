"""Workflow version history controller -- list, get, diff, rollback."""

from typing import Annotated, Final

from litestar import Controller, Response, get, post
from litestar.datastructures import State  # noqa: TC002
from litestar.params import PathParameter, QueryParameter

from synthorg.api.auth import get_authenticated_user_id
from synthorg.api.cursor import decode_cursor
from synthorg.api.dto import ApiResponse, PaginatedResponse
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    encode_repo_seek_meta,
)
from synthorg.api.path_params import PathId  # noqa: TC001
from synthorg.core.domain_errors import (
    NotFoundError,
    ValidationError,
    VersionConflictError,
)
from synthorg.core.persistence_errors import (
    PersistenceVersionConflictError,
)
from synthorg.engine.workflow.definition import (
    WorkflowDefinition,
)
from synthorg.engine.workflow.diff import WorkflowDiff, compute_diff
from synthorg.observability import get_logger
from synthorg.observability.events.workflow_definition import (
    WORKFLOW_DEF_DIFF_COMPUTED,
    WORKFLOW_DEF_INVALID_REQUEST,
    WORKFLOW_DEF_NOT_FOUND,
    WORKFLOW_DEF_VERSION_CONFLICT,
    WORKFLOW_DEF_VERSION_LISTED,
)
from synthorg.versioning import VersionSnapshot
from synthorg.versioning.models import (
    RollbackWorkflowRequest,  # noqa: TC001 -- Litestar runtime request-body
)

logger = get_logger(__name__)
_DEFAULT_LIMIT: Final[int] = 20

SnapshotT = VersionSnapshot[WorkflowDefinition]


class WorkflowVersionController(Controller):
    """Version history, diff, and rollback for workflow definitions."""

    path = "/workflows"
    tags = ("workflows",)

    @get("/{workflow_id:str}/versions", guards=[require_read_access])
    async def list_versions(
        self,
        state: State,
        workflow_id: PathId,
        cursor: CursorParam = None,
        limit: CursorLimit = _DEFAULT_LIMIT,
    ) -> Response[PaginatedResponse[SnapshotT]]:
        """List version history for a workflow definition.

        Returns:
            ``Response[PaginatedResponse[SnapshotT]]`` instance.
        """
        secret = state.app_state.cursor_secret
        offset = 0 if cursor is None else decode_cursor(cursor, secret=secret)
        versions, total = await state.app_state.workflow_version_service.list_versions(
            workflow_id,
            limit=limit,
            offset=offset,
        )
        logger.debug(
            WORKFLOW_DEF_VERSION_LISTED,
            definition_id=workflow_id,
            count=len(versions),
        )
        meta = encode_repo_seek_meta(
            offset=offset,
            page_len=len(versions),
            total=total,
            limit=limit,
            secret=secret,
        )
        return Response(
            content=PaginatedResponse[SnapshotT](
                data=versions,
                pagination=meta,
            ),
        )

    @get(
        "/{workflow_id:str}/versions/{version_num:int}",
        guards=[require_read_access],
    )
    async def get_version(
        self,
        state: State,
        workflow_id: PathId,
        version_num: Annotated[
            int,
            PathParameter(
                ge=1,
                description="Workflow version (one-based; 1 = first revision).",
            ),
        ],
    ) -> Response[ApiResponse[SnapshotT]]:
        """Get a specific version snapshot.

        Returns:
            ``Response[ApiResponse[SnapshotT]]`` instance.

        Raises:
            NotFoundError: Raised on the corresponding failure path.
        """
        version = await state.app_state.workflow_version_service.get_version(
            workflow_id,
            version_num,
        )
        if version is None:
            logger.warning(
                WORKFLOW_DEF_NOT_FOUND,
                definition_id=workflow_id,
                version=version_num,
            )
            msg = f"Version {version_num} not found"
            raise NotFoundError(msg)
        return Response(
            content=ApiResponse[SnapshotT](data=version),
        )

    @get("/{workflow_id:str}/diff", guards=[require_read_access])
    async def get_diff(
        self,
        state: State,
        workflow_id: PathId,
        from_version: Annotated[
            int,
            QueryParameter(
                required=True,
                ge=1,
                description="Source version",
            ),
        ],
        to_version: Annotated[
            int,
            QueryParameter(
                required=True,
                ge=1,
                description="Target version",
            ),
        ],
    ) -> Response[ApiResponse[WorkflowDiff]]:
        """Compute diff between two versions of a workflow definition.

        Returns:
            ``Response[ApiResponse[WorkflowDiff]]`` instance.

        Raises:
            ValidationError: Raised on the corresponding failure path.
        """
        if from_version == to_version:
            logger.warning(
                WORKFLOW_DEF_INVALID_REQUEST,
                definition_id=workflow_id,
                error="from_version and to_version must differ",
            )
            msg = "from_version and to_version must differ"
            raise ValidationError(msg)

        version_service = state.app_state.workflow_version_service
        old, new = await version_service.get_version_pair_or_404(
            workflow_id,
            from_version,
            to_version,
        )
        diff = compute_diff(old, new)
        logger.debug(
            WORKFLOW_DEF_DIFF_COMPUTED,
            definition_id=workflow_id,
            from_version=from_version,
            to_version=to_version,
        )
        return Response(
            content=ApiResponse[WorkflowDiff](data=diff),
        )

    @post(
        "/{workflow_id:str}/rollback",
        guards=[require_write_access],
        status_code=200,
    )
    async def rollback_workflow(
        self,
        state: State,
        workflow_id: PathId,
        data: RollbackWorkflowRequest,
    ) -> Response[ApiResponse[WorkflowDefinition]]:
        """Rollback a workflow to a previous version.

        Returns:
            Result matching the declared return annotation.

        Raises:
            VersionConflictError: Raised on the corresponding failure path.
        """
        rollback_service = state.app_state.workflow_rollback_service
        try:
            rolled_back = await rollback_service.prepare_rollback(
                workflow_id,
                data,
                saved_by=get_authenticated_user_id(),
            )
        except PersistenceVersionConflictError as exc:
            # Translate the persistence-layer name to the API-aware
            # ``VersionConflictError`` so the centralised handler emits
            # the user-facing message rather than the lower-level
            # "Optimistic concurrency conflict" default. The
            # rollback-specific context (``definition_id`` /
            # ``target_version``) only lives in scope at the controller
            # boundary; the centralised handler captures exception
            # attributes but not call-site context.
            logger.warning(
                WORKFLOW_DEF_VERSION_CONFLICT,
                definition_id=workflow_id,
                target_version=data.target_version,
                error_type=type(exc).__name__,
            )
            msg = "Version conflict during rollback. Reload and retry."
            raise VersionConflictError(msg) from exc

        return Response(
            content=ApiResponse[WorkflowDefinition](data=rolled_back),
        )
