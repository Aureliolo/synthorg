"""Postgres repository implementation for ProjectEnvironment."""

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import ValidationError

from synthorg.core.persistence_errors import QueryError
from synthorg.core.project_enums import EnvironmentType
from synthorg.core.project_environment import ProjectEnvironment
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.project_environment import (
    PERSISTENCE_PROJECT_ENVIRONMENT_DELETE_FAILED,
    PERSISTENCE_PROJECT_ENVIRONMENT_DESERIALIZE_FAILED,
    PERSISTENCE_PROJECT_ENVIRONMENT_FETCH_FAILED,
    PERSISTENCE_PROJECT_ENVIRONMENT_FETCHED,
    PERSISTENCE_PROJECT_ENVIRONMENT_LIST_FAILED,
    PERSISTENCE_PROJECT_ENVIRONMENT_LISTED,
    PERSISTENCE_PROJECT_ENVIRONMENT_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import coerce_row_timestamp
from synthorg.persistence._shared.pagination import validate_pagination_args

logger = get_logger(__name__)

_MAX_LIST_ROWS: int = 10_000


def _row_to_environment(row: DictRow) -> ProjectEnvironment:
    """Reconstruct a ``ProjectEnvironment`` from a Postgres dict_row.

    Returns:
        Result of type ``ProjectEnvironment``.
    """
    data = dict(row)
    data["environment_type"] = EnvironmentType(data["environment_type"])
    data["provisioned_at"] = coerce_row_timestamp(data["provisioned_at"])
    data["updated_at"] = coerce_row_timestamp(data["updated_at"])
    return ProjectEnvironment.model_validate(data)


class PostgresProjectEnvironmentRepository:
    """Postgres-backed project environment repository.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    @staticmethod
    def _row_params(environment: ProjectEnvironment) -> tuple[object, ...]:
        """Row params.

        Returns:
            Tuple of scalar SQL parameter values for INSERT/UPDATE.
        """
        return (
            environment.project_id,
            environment.environment_type.value,
            environment.declaration_hash,
            environment.image_ref,
            environment.provisioned_at,
            environment.updated_at,
        )

    async def save(self, entity: ProjectEnvironment) -> None:
        """Persist a project environment via upsert (insert or update).

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO project_environments (project_id,
                                                      environment_type,
                                                      declaration_hash, image_ref,
                                                      provisioned_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    ON CONFLICT(project_id) DO UPDATE SET
                        environment_type=EXCLUDED.environment_type,
                        declaration_hash=EXCLUDED.declaration_hash,
                        image_ref=EXCLUDED.image_ref,
                        provisioned_at=EXCLUDED.provisioned_at,
                        updated_at=EXCLUDED.updated_at
                    """,
                    self._row_params(entity),
                )
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to save project environment {entity.project_id!r}"
            logger.warning(
                PERSISTENCE_PROJECT_ENVIRONMENT_SAVE_FAILED,
                project_id=entity.project_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def get(self, entity_id: NotBlankStr) -> ProjectEnvironment | None:
        """Retrieve a project environment by owning project id.

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
                    "SELECT project_id, environment_type, declaration_hash, "
                    "image_ref, provisioned_at, updated_at "
                    "FROM project_environments WHERE project_id = %s",
                    (entity_id,),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Failed to fetch project environment {entity_id!r}"
            logger.warning(
                PERSISTENCE_PROJECT_ENVIRONMENT_FETCH_FAILED,
                project_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            logger.debug(
                PERSISTENCE_PROJECT_ENVIRONMENT_FETCHED,
                project_id=entity_id,
                found=False,
            )
            return None
        try:
            environment = _row_to_environment(row)
        except (ValueError, ValidationError, KeyError) as exc:
            msg = f"Failed to deserialize project environment {entity_id!r}"
            logger.warning(
                PERSISTENCE_PROJECT_ENVIRONMENT_DESERIALIZE_FAILED,
                project_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(
            PERSISTENCE_PROJECT_ENVIRONMENT_FETCHED,
            project_id=entity_id,
            found=True,
        )
        return environment

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ProjectEnvironment, ...]:
        """List environments in project-id order.

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_PROJECT_ENVIRONMENT_LIST_FAILED
        )
        effective_limit = min(limit, _MAX_LIST_ROWS)
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT project_id, environment_type, declaration_hash, "
                    "image_ref, provisioned_at, updated_at "
                    "FROM project_environments "
                    "ORDER BY project_id LIMIT %s OFFSET %s",
                    (effective_limit, offset),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to list project environments"
            logger.warning(
                PERSISTENCE_PROJECT_ENVIRONMENT_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        try:
            environments = tuple(_row_to_environment(row) for row in rows)
        except (ValueError, ValidationError, KeyError) as exc:
            msg = "Failed to deserialize project environments"
            logger.warning(
                PERSISTENCE_PROJECT_ENVIRONMENT_DESERIALIZE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_PROJECT_ENVIRONMENT_LISTED, count=len(environments))
        return environments

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a project environment by owning project id.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM project_environments WHERE project_id = %s",
                    (entity_id,),
                )
                deleted = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to delete project environment {entity_id!r}"
            logger.warning(
                PERSISTENCE_PROJECT_ENVIRONMENT_DELETE_FAILED,
                project_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return deleted
