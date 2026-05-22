"""SQLite repository implementation for ProjectEnvironment."""

import sqlite3
from typing import TYPE_CHECKING

import aiosqlite
from pydantic import ValidationError

from synthorg.core.enums import EnvironmentType
from synthorg.core.persistence_errors import QueryError
from synthorg.core.project_environment import ProjectEnvironment
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_PROJECT_ENVIRONMENT_DELETE_FAILED,
    PERSISTENCE_PROJECT_ENVIRONMENT_DESERIALIZE_FAILED,
    PERSISTENCE_PROJECT_ENVIRONMENT_FETCH_FAILED,
    PERSISTENCE_PROJECT_ENVIRONMENT_FETCHED,
    PERSISTENCE_PROJECT_ENVIRONMENT_LIST_FAILED,
    PERSISTENCE_PROJECT_ENVIRONMENT_LISTED,
    PERSISTENCE_PROJECT_ENVIRONMENT_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import coerce_row_timestamp, format_iso_utc
from synthorg.persistence._shared.pagination import validate_pagination_args

if TYPE_CHECKING:
    from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)

_MAX_LIST_ROWS: int = 10_000


def _row_to_environment(row: aiosqlite.Row) -> ProjectEnvironment:
    """Reconstruct a ``ProjectEnvironment`` from a database row."""
    data = dict(row)
    data["environment_type"] = EnvironmentType(data["environment_type"])
    data["provisioned_at"] = coerce_row_timestamp(data["provisioned_at"])
    data["updated_at"] = coerce_row_timestamp(data["updated_at"])
    return ProjectEnvironment.model_validate(data)


class SQLiteProjectEnvironmentRepository:
    """SQLite-backed project environment repository.

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
    def _row_params(environment: ProjectEnvironment) -> tuple[object, ...]:
        return (
            environment.project_id,
            environment.environment_type.value,
            environment.declaration_hash,
            environment.image_ref,
            format_iso_utc(environment.provisioned_at),
            format_iso_utc(environment.updated_at),
        )

    async def _safe_rollback(self, *, event: str) -> None:
        """Best-effort rollback on the shared connection.

        The ``event`` parameter identifies the originating call site
        (save vs delete) so rollback failures log the right event.
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

    async def save(self, entity: ProjectEnvironment) -> None:
        """Persist a project environment via upsert (insert or update)."""
        async with self._write_context():
            try:
                await self._db.execute(
                    """\
INSERT INTO project_environments (project_id, environment_type,
                                  declaration_hash, image_ref,
                                  provisioned_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?)
ON CONFLICT(project_id) DO UPDATE SET
    environment_type=excluded.environment_type,
    declaration_hash=excluded.declaration_hash,
    image_ref=excluded.image_ref,
    provisioned_at=excluded.provisioned_at,
    updated_at=excluded.updated_at""",
                    self._row_params(entity),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback(
                    event=PERSISTENCE_PROJECT_ENVIRONMENT_SAVE_FAILED
                )
                msg = f"Failed to save project environment {entity.project_id!r}"
                logger.warning(
                    PERSISTENCE_PROJECT_ENVIRONMENT_SAVE_FAILED,
                    project_id=entity.project_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def get(self, entity_id: NotBlankStr) -> ProjectEnvironment | None:
        """Retrieve a project environment by owning project id."""
        try:
            cursor = await self._db.execute(
                "SELECT project_id, environment_type, declaration_hash, "
                "image_ref, provisioned_at, updated_at "
                "FROM project_environments WHERE project_id = ?",
                (entity_id,),
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
        """List environments in project-id order."""
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_PROJECT_ENVIRONMENT_LIST_FAILED
        )
        effective_limit = min(limit, _MAX_LIST_ROWS)
        try:
            cursor = await self._db.execute(
                "SELECT project_id, environment_type, declaration_hash, "
                "image_ref, provisioned_at, updated_at "
                "FROM project_environments "
                "ORDER BY project_id LIMIT ? OFFSET ?",
                (effective_limit, offset),
            )
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
        """Delete a project environment by owning project id."""
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "DELETE FROM project_environments WHERE project_id = ?",
                    (entity_id,),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback(
                    event=PERSISTENCE_PROJECT_ENVIRONMENT_DELETE_FAILED
                )
                msg = f"Failed to delete project environment {entity_id!r}"
                logger.warning(
                    PERSISTENCE_PROJECT_ENVIRONMENT_DELETE_FAILED,
                    project_id=entity_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
            return cursor.rowcount > 0
