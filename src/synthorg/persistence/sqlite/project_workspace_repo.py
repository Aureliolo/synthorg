"""SQLite repository implementation for ProjectWorkspace."""

import sqlite3

import aiosqlite
from pydantic import ValidationError

from synthorg.core.persistence_errors import QueryError
from synthorg.core.project_enums import GitBackendType
from synthorg.core.project_workspace import ProjectWorkspace
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.project_workspace import (
    PERSISTENCE_PROJECT_WORKSPACE_DELETE_FAILED,
    PERSISTENCE_PROJECT_WORKSPACE_DESERIALIZE_FAILED,
    PERSISTENCE_PROJECT_WORKSPACE_FETCH_FAILED,
    PERSISTENCE_PROJECT_WORKSPACE_FETCHED,
    PERSISTENCE_PROJECT_WORKSPACE_LIST_FAILED,
    PERSISTENCE_PROJECT_WORKSPACE_LISTED,
    PERSISTENCE_PROJECT_WORKSPACE_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import coerce_row_timestamp, format_iso_utc
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)

_MAX_LIST_ROWS: int = 10_000


def _row_to_workspace(row: aiosqlite.Row) -> ProjectWorkspace:
    """Reconstruct a ``ProjectWorkspace`` from a database row.

    Returns:
        Result of type ``ProjectWorkspace``.
    """
    data = dict(row)
    data["git_backend_kind"] = GitBackendType(data["git_backend_kind"])
    data["created_at"] = coerce_row_timestamp(data["created_at"])
    data["updated_at"] = coerce_row_timestamp(data["updated_at"])
    return ProjectWorkspace.model_validate(data)


class SQLiteProjectWorkspaceRepository:
    """SQLite-backed project workspace repository.

    Args:
        db: An open aiosqlite connection with ``row_factory`` set to
            ``aiosqlite.Row``.
        write_context: Async context manager that serializes writes on
            the shared connection.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._write_context = write_context

    @staticmethod
    def _row_params(workspace: ProjectWorkspace) -> tuple[object, ...]:
        """Row params.

        Returns:
            Tuple of scalar SQL parameter values for INSERT/UPDATE.
        """
        return (
            workspace.project_id,
            workspace.workspace_path,
            workspace.git_backend_kind.value,
            workspace.remote_ref,
            workspace.default_branch,
            format_iso_utc(workspace.created_at),
            format_iso_utc(workspace.updated_at),
        )

    async def _safe_rollback(self, *, event: str) -> None:
        """Best-effort rollback on the shared connection.

        The ``event`` parameter identifies the originating call site
        (save vs delete) so rollback failures log the right event;
        without it, delete-path rollback failures would mislabel as
        save failures.
        """
        try:
            await self._db.rollback()
        except (sqlite3.Error, aiosqlite.Error) as rollback_exc:
            logger.warning(
                event,
                error_type=type(rollback_exc).__name__,
                error=safe_error_description(rollback_exc),
                rollback_failed=True,
            )

    async def save(self, entity: ProjectWorkspace) -> None:
        """Persist a project workspace via upsert (insert or update).

        Raises:
            QueryError: If the database query fails.
        """
        async with self._write_context():
            try:
                await self._db.execute(
                    """\
INSERT INTO project_workspaces (project_id, workspace_path,
                                git_backend_kind, remote_ref,
                                default_branch, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(project_id) DO UPDATE SET
    workspace_path=excluded.workspace_path,
    git_backend_kind=excluded.git_backend_kind,
    remote_ref=excluded.remote_ref,
    default_branch=excluded.default_branch,
    created_at=excluded.created_at,
    updated_at=excluded.updated_at""",
                    self._row_params(entity),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback(
                    event=PERSISTENCE_PROJECT_WORKSPACE_SAVE_FAILED
                )
                msg = f"Failed to save project workspace {entity.project_id!r}"
                logger.warning(
                    PERSISTENCE_PROJECT_WORKSPACE_SAVE_FAILED,
                    project_id=entity.project_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def get(self, entity_id: NotBlankStr) -> ProjectWorkspace | None:
        """Retrieve a project workspace by owning project id.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._db.execute(
                "SELECT * FROM project_workspaces WHERE project_id = ?",
                (entity_id,),
            ) as cursor:
                row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to fetch project workspace {entity_id!r}"
            logger.warning(
                PERSISTENCE_PROJECT_WORKSPACE_FETCH_FAILED,
                project_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            logger.debug(
                PERSISTENCE_PROJECT_WORKSPACE_FETCHED,
                project_id=entity_id,
                found=False,
            )
            return None
        try:
            workspace = _row_to_workspace(row)
        except (ValueError, ValidationError, KeyError) as exc:
            msg = f"Failed to deserialize project workspace {entity_id!r}"
            logger.warning(
                PERSISTENCE_PROJECT_WORKSPACE_DESERIALIZE_FAILED,
                project_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(
            PERSISTENCE_PROJECT_WORKSPACE_FETCHED,
            project_id=entity_id,
            found=True,
        )
        return workspace

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ProjectWorkspace, ...]:
        """List workspaces in project-id order.

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_PROJECT_WORKSPACE_LIST_FAILED
        )
        effective_limit = min(limit, _MAX_LIST_ROWS)
        try:
            async with self._db.execute(
                "SELECT * FROM project_workspaces ORDER BY project_id LIMIT ? OFFSET ?",
                (effective_limit, offset),
            ) as cursor:
                rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to list project workspaces"
            logger.warning(
                PERSISTENCE_PROJECT_WORKSPACE_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        try:
            workspaces = tuple(_row_to_workspace(row) for row in rows)
        except (ValueError, ValidationError, KeyError) as exc:
            msg = "Failed to deserialize project workspaces"
            logger.warning(
                PERSISTENCE_PROJECT_WORKSPACE_DESERIALIZE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_PROJECT_WORKSPACE_LISTED, count=len(workspaces))
        return workspaces

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a project workspace by owning project id.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        async with self._write_context():
            try:
                async with self._db.execute(
                    "DELETE FROM project_workspaces WHERE project_id = ?",
                    (entity_id,),
                ) as cursor:
                    await self._db.commit()
                    _db_rowcount = cursor.rowcount
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback(
                    event=PERSISTENCE_PROJECT_WORKSPACE_DELETE_FAILED
                )
                msg = f"Failed to delete project workspace {entity_id!r}"
                logger.warning(
                    PERSISTENCE_PROJECT_WORKSPACE_DELETE_FAILED,
                    project_id=entity_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
            return _db_rowcount > 0
