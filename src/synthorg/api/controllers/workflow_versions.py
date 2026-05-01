"""Workflow version history controller -- list, get, diff, rollback."""

import asyncio
from datetime import UTC, datetime
from typing import Annotated, Any

from litestar import Controller, Request, Response, get, post
from litestar.datastructures import State  # noqa: TC002
from litestar.params import Parameter

from synthorg.api.controllers._workflow_helpers import get_auth_user_id
from synthorg.api.cursor import decode_cursor
from synthorg.api.dto import (
    ApiResponse,
    PaginatedResponse,
    RollbackWorkflowRequest,
)
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.pagination import (
    CursorLimit,
    CursorParam,
    encode_repo_seek_meta,
)
from synthorg.api.path_params import PathId  # noqa: TC001
from synthorg.core.persistence_errors import PersistenceError, VersionConflictError
from synthorg.engine.workflow.definition import (
    WorkflowDefinition,
)
from synthorg.engine.workflow.diff import WorkflowDiff, compute_diff
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.workflow_definition import (
    WORKFLOW_DEF_DIFF_COMPUTED,
    WORKFLOW_DEF_INVALID_REQUEST,
    WORKFLOW_DEF_NOT_FOUND,
    WORKFLOW_DEF_ROLLED_BACK,
    WORKFLOW_DEF_VERSION_CONFLICT,
    WORKFLOW_DEF_VERSION_LISTED,
)
from synthorg.observability.events.workflow_version import (
    WORKFLOW_VERSION_SNAPSHOT_FAILED,
)
from synthorg.persistence.version_repo import VersionRepository  # noqa: TC001
from synthorg.persistence.workflow_definition_repo import (
    WorkflowDefinitionRepository,  # noqa: TC001
)
from synthorg.versioning import VersioningService, VersionSnapshot

logger = get_logger(__name__)

SnapshotT = VersionSnapshot[WorkflowDefinition]


async def _fetch_version_pair(
    version_repo: VersionRepository[WorkflowDefinition],
    workflow_id: str,
    from_version: int,
    to_version: int,
) -> tuple[SnapshotT, SnapshotT] | Response[ApiResponse[WorkflowDiff]]:
    """Fetch two version snapshots, returning an error response on failure.

    Args:
        version_repo: The workflow version repository.
        workflow_id: The workflow definition ID.
        from_version: Source version number.
        to_version: Target version number.

    Returns:
        A tuple ``(old, new)`` on success, or a ``Response`` error if
        either version is not found.
    """
    old = await version_repo.get_version(workflow_id, from_version)
    if old is None:
        logger.warning(
            WORKFLOW_DEF_NOT_FOUND,
            definition_id=workflow_id,
            version=from_version,
        )
        return Response(
            content=ApiResponse[WorkflowDiff](
                error=f"Version {from_version} not found",
            ),
            status_code=404,
        )
    new = await version_repo.get_version(workflow_id, to_version)
    if new is None:
        logger.warning(
            WORKFLOW_DEF_NOT_FOUND,
            definition_id=workflow_id,
            version=to_version,
        )
        return Response(
            content=ApiResponse[WorkflowDiff](
                error=f"Version {to_version} not found",
            ),
            status_code=404,
        )
    return old, new


async def _fetch_rollback_target(
    repo: WorkflowDefinitionRepository,
    version_repo: VersionRepository[WorkflowDefinition],
    workflow_id: str,
    data: RollbackWorkflowRequest,
) -> tuple[WorkflowDefinition, SnapshotT] | Response[ApiResponse[WorkflowDefinition]]:
    """Look up the definition and target version for a rollback.

    Validates that the definition exists, the expected version matches,
    and the target version snapshot exists.

    Args:
        repo: The workflow definition repository.
        version_repo: The workflow version repository.
        workflow_id: The workflow definition ID.
        data: The rollback request payload.

    Returns:
        A tuple ``(existing, target)`` on success, or a ``Response``
        error on any validation failure.
    """
    existing = await repo.get(workflow_id)
    if existing is None:
        logger.warning(
            WORKFLOW_DEF_NOT_FOUND,
            definition_id=workflow_id,
        )
        return Response(
            content=ApiResponse[WorkflowDefinition](
                error="Workflow definition not found",
            ),
            status_code=404,
        )

    if data.expected_revision != existing.revision:
        logger.warning(
            WORKFLOW_DEF_VERSION_CONFLICT,
            definition_id=workflow_id,
            expected=data.expected_revision,
            actual=existing.revision,
        )
        return Response(
            content=ApiResponse[WorkflowDefinition](
                error=(
                    "Version conflict: the workflow was modified. Reload and retry."
                ),
            ),
            status_code=409,
        )

    target = await version_repo.get_version(
        workflow_id,
        data.target_version,
    )
    if target is None:
        logger.warning(
            WORKFLOW_DEF_NOT_FOUND,
            definition_id=workflow_id,
            version=data.target_version,
        )
        return Response(
            content=ApiResponse[WorkflowDefinition](
                error=f"Target version {data.target_version} not found",
            ),
            status_code=404,
        )

    return existing, target


def _build_rolled_back_definition(
    existing: WorkflowDefinition,
    target: SnapshotT,
    now: datetime,
) -> WorkflowDefinition:
    """Build a new definition that restores a target version's content.

    Args:
        existing: The current persisted definition.
        target: The version snapshot to restore.
        now: Current UTC timestamp.

    Returns:
        A ``WorkflowDefinition`` with version bumped and content
        restored from *target*.
    """
    return target.snapshot.model_copy(
        update={
            "id": existing.id,
            "version": existing.version,
            "created_by": existing.created_by,
            "created_at": existing.created_at,
            "updated_at": now,
            "revision": existing.revision + 1,
        },
        deep=True,
    )


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
        limit: CursorLimit = 20,
    ) -> Response[PaginatedResponse[SnapshotT]]:
        """List version history for a workflow definition."""
        secret = state.app_state.cursor_secret
        offset = 0 if cursor is None else decode_cursor(cursor, secret=secret)
        version_repo = state.app_state.persistence.workflow_versions
        async with asyncio.TaskGroup() as tg:
            list_task = tg.create_task(
                version_repo.list_versions(
                    workflow_id,
                    limit=limit,
                    offset=offset,
                ),
            )
            count_task = tg.create_task(
                version_repo.count_versions(workflow_id),
            )
        versions = list_task.result()
        total = count_task.result()
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
        version_num: Annotated[int, Parameter(ge=1)],
    ) -> Response[ApiResponse[SnapshotT]]:
        """Get a specific version snapshot."""
        version_repo = state.app_state.persistence.workflow_versions
        version = await version_repo.get_version(
            workflow_id,
            version_num,
        )
        if version is None:
            logger.warning(
                WORKFLOW_DEF_NOT_FOUND,
                definition_id=workflow_id,
                version=version_num,
            )
            return Response(
                content=ApiResponse[SnapshotT](
                    error=f"Version {version_num} not found",
                ),
                status_code=404,
            )
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
            Parameter(
                required=True,
                ge=1,
                description="Source version",
            ),
        ],
        to_version: Annotated[
            int,
            Parameter(
                required=True,
                ge=1,
                description="Target version",
            ),
        ],
    ) -> Response[ApiResponse[WorkflowDiff]]:
        """Compute diff between two versions of a workflow definition."""
        if from_version == to_version:
            logger.warning(
                WORKFLOW_DEF_INVALID_REQUEST,
                definition_id=workflow_id,
                error="from_version and to_version must differ",
            )
            return Response(
                content=ApiResponse[WorkflowDiff](
                    error="from_version and to_version must differ",
                ),
                status_code=400,
            )

        version_repo = state.app_state.persistence.workflow_versions
        result = await _fetch_version_pair(
            version_repo,
            workflow_id,
            from_version,
            to_version,
        )
        if isinstance(result, Response):
            return result
        old, new = result

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
        request: Request[Any, Any, Any],
        state: State,
        workflow_id: PathId,
        data: RollbackWorkflowRequest,
    ) -> Response[ApiResponse[WorkflowDefinition]]:
        """Rollback a workflow to a previous version."""
        repo = state.app_state.persistence.workflow_definitions
        version_repo = state.app_state.persistence.workflow_versions

        result = await _fetch_rollback_target(
            repo,
            version_repo,
            workflow_id,
            data,
        )
        if isinstance(result, Response):
            return result
        existing, target = result
        updater = get_auth_user_id(request)
        rolled_back = _build_rolled_back_definition(
            existing,
            target,
            datetime.now(UTC),
        )

        try:
            await repo.save(rolled_back)
        except VersionConflictError as exc:
            logger.warning(
                WORKFLOW_DEF_VERSION_CONFLICT,
                definition_id=workflow_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return Response(
                content=ApiResponse[WorkflowDefinition](
                    error=("Version conflict during rollback. Reload and retry."),
                ),
                status_code=409,
            )

        svc = VersioningService(version_repo)
        try:
            await svc.snapshot_if_changed(
                entity_id=rolled_back.id,
                snapshot=rolled_back,
                saved_by=updater,
            )
        except PersistenceError as exc:
            # Best-effort: a snapshot failure here does not block the
            # rollback. Use a sanitized warning per SEC-1 instead of
            # ``logger.exception`` (which can leak locals).
            logger.warning(
                WORKFLOW_VERSION_SNAPSHOT_FAILED,
                definition_id=rolled_back.id,
                revision=rolled_back.revision,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
        logger.info(
            WORKFLOW_DEF_ROLLED_BACK,
            definition_id=workflow_id,
            target_version=data.target_version,
            new_revision=rolled_back.revision,
        )
        return Response(
            content=ApiResponse[WorkflowDefinition](data=rolled_back),
        )
