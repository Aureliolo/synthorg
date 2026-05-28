"""Postgres repository implementation for ProjectWorkspace."""

from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.rows import dict_row
from pydantic import ValidationError

from synthorg.core.enums import GitBackendType
from synthorg.core.persistence_errors import QueryError
from synthorg.core.project_workspace import ProjectWorkspace
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_PROJECT_WORKSPACE_DELETE_FAILED,
    PERSISTENCE_PROJECT_WORKSPACE_DESERIALIZE_FAILED,
    PERSISTENCE_PROJECT_WORKSPACE_FETCH_FAILED,
    PERSISTENCE_PROJECT_WORKSPACE_FETCHED,
    PERSISTENCE_PROJECT_WORKSPACE_LIST_FAILED,
    PERSISTENCE_PROJECT_WORKSPACE_LISTED,
    PERSISTENCE_PROJECT_WORKSPACE_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import coerce_row_timestamp
from synthorg.persistence._shared.pagination import validate_pagination_args

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = get_logger(__name__)

_MAX_LIST_ROWS: int = 10_000


def _row_to_workspace(row: dict[str, Any]) -> ProjectWorkspace:
    """Reconstruct a ``ProjectWorkspace`` from a Postgres dict_row.

    Returns:
        Result of type ``ProjectWorkspace``.
    """
    data = dict(row)
    data["git_backend_kind"] = GitBackendType(data["git_backend_kind"])
    data["created_at"] = coerce_row_timestamp(data["created_at"])
    data["updated_at"] = coerce_row_timestamp(data["updated_at"])
    return ProjectWorkspace.model_validate(data)


class PostgresProjectWorkspaceRepository:
    """Postgres-backed project workspace repository.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

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
            workspace.created_at,
            workspace.updated_at,
        )

    async def save(self, entity: ProjectWorkspace) -> None:
        """Persist a project workspace via upsert (insert or update).

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO project_workspaces (project_id, workspace_path,
                                                    git_backend_kind, remote_ref,
                                                    default_branch, created_at,
                                                    updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT(project_id) DO UPDATE SET
                        workspace_path=EXCLUDED.workspace_path,
                        git_backend_kind=EXCLUDED.git_backend_kind,
                        remote_ref=EXCLUDED.remote_ref,
                        default_branch=EXCLUDED.default_branch,
                        created_at=EXCLUDED.created_at,
                        updated_at=EXCLUDED.updated_at
                    """,
                    self._row_params(entity),
                )
                await conn.commit()
        except psycopg.Error as exc:
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
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT * FROM project_workspaces WHERE project_id = %s",
                    (entity_id,),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
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
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT * FROM project_workspaces "
                    "ORDER BY project_id LIMIT %s OFFSET %s",
                    (effective_limit, offset),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
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
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM project_workspaces WHERE project_id = %s",
                    (entity_id,),
                )
                deleted = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to delete project workspace {entity_id!r}"
            logger.warning(
                PERSISTENCE_PROJECT_WORKSPACE_DELETE_FAILED,
                project_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return deleted
