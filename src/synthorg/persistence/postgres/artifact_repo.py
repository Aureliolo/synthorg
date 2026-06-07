"""Postgres implementation of the ArtifactRepository protocol.

This is the Postgres sibling of src/synthorg/persistence/sqlite/artifact_repo.py.
Postgres stores timestamps as native TIMESTAMPTZ columns (SQLite uses ISO 8601
strings). The repository converts to and from ISO strings at the boundary so
the protocol surface remains identical for both backends.
"""

from datetime import UTC
from typing import TYPE_CHECKING

import psycopg
from psycopg.rows import dict_row
from pydantic import ValidationError

from synthorg.core.artifact import Artifact
from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.artifact import (
    PERSISTENCE_ARTIFACT_DELETE_FAILED,
    PERSISTENCE_ARTIFACT_DESERIALIZE_FAILED,
    PERSISTENCE_ARTIFACT_FETCH_FAILED,
    PERSISTENCE_ARTIFACT_FETCHED,
    PERSISTENCE_ARTIFACT_LIST_FAILED,
    PERSISTENCE_ARTIFACT_LISTED,
    PERSISTENCE_ARTIFACT_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence.artifact_protocol import ArtifactFilterSpec

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = get_logger(__name__)

_MAX_LIST_ROWS: int = 10_000


class PostgresArtifactRepository:
    """Postgres-backed artifact repository.

    Provides CRUD operations for ``Artifact`` models using a shared
    ``psycopg_pool.AsyncConnectionPool``.  All write operations
    commit immediately.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, entity: Artifact) -> None:
        """Persist an artifact (insert or update by id).

        Args:
            entity: Artifact model to persist.

        Raises:
            QueryError: If the database operation fails.
        """
        await self.save_returning_outcome(entity)

    async def save_returning_outcome(self, artifact: Artifact) -> bool:
        """Persist an artifact atomically; return whether it was inserted.

        Uses Postgres' ``RETURNING (xmax = 0) AS created`` trick on
        an upsert to derive the lifecycle outcome inside the same
        atomic operation -- no TOCTOU ``get`` + ``save`` race.
        ``xmax = 0`` indicates the row was freshly inserted; any
        other ``xmax`` value indicates the conflict path (UPDATE
        SET ...) ran instead.

        Args:
            artifact: Artifact model to persist.

        Returns:
            ``True`` when this call inserted a new row, ``False`` when
            it updated an existing row in place.

        Raises:
            QueryError: If the database operation fails.
        """
        created_at_dt = (
            artifact.created_at.astimezone(UTC) if artifact.created_at else None
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """\
INSERT INTO artifacts (id, type, path, task_id, created_by,
                       description, content_type, size_bytes,
                       created_at, project_id)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT(id) DO UPDATE SET
    type=EXCLUDED.type,
    path=EXCLUDED.path,
    task_id=EXCLUDED.task_id,
    created_by=EXCLUDED.created_by,
    description=EXCLUDED.description,
    content_type=EXCLUDED.content_type,
    size_bytes=EXCLUDED.size_bytes,
    created_at=EXCLUDED.created_at,
    project_id=EXCLUDED.project_id
RETURNING (xmax = 0) AS created""",
                    (
                        artifact.id,
                        artifact.type.value,
                        artifact.path,
                        artifact.task_id,
                        artifact.created_by,
                        artifact.description,
                        artifact.content_type,
                        artifact.size_bytes,
                        created_at_dt,
                        artifact.project_id,
                    ),
                )
                row = await cur.fetchone()
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to save artifact {artifact.id!r}"
            logger.warning(
                PERSISTENCE_ARTIFACT_SAVE_FAILED,
                artifact_id=artifact.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        # ``RETURNING (xmax = 0) AS created`` yields exactly one row
        # on every successful upsert -- one for INSERT, one for the
        # ON CONFLICT DO UPDATE branch.  A missing row therefore means
        # the driver returned no result, which is a hard failure;
        # silently coercing it to ``False`` ("updated") would mislabel
        # creates as updates and let callers like ``ArtifactService.save``
        # emit the wrong audit event.  Fail closed instead.
        if row is None:
            msg = (
                f"Failed to save artifact {artifact.id!r}: no row returned from upsert"
            )
            logger.warning(
                PERSISTENCE_ARTIFACT_SAVE_FAILED,
                artifact_id=artifact.id,
                error_type="MissingReturningRow",
                error=msg,
            )
            raise QueryError(msg)
        return bool(row[0])

    async def get(self, entity_id: NotBlankStr) -> Artifact | None:
        """Retrieve an artifact by primary key.

        Args:
            entity_id: Unique artifact identifier.

        Returns:
            The matching ``Artifact``, or ``None`` if not found.

        Raises:
            QueryError: If the database query or deserialization fails.
        """
        artifact_id = entity_id
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT id, type, path, task_id, created_by, "
                    "description, content_type, size_bytes, created_at, "
                    "project_id "
                    "FROM artifacts WHERE id = %s",
                    (artifact_id,),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Failed to fetch artifact {artifact_id!r}"
            logger.warning(
                PERSISTENCE_ARTIFACT_FETCH_FAILED,
                artifact_id=artifact_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        if row is None:
            logger.debug(
                PERSISTENCE_ARTIFACT_FETCHED, artifact_id=artifact_id, found=False
            )
            return None

        try:
            artifact = Artifact.model_validate(row)
        except (ValueError, ValidationError, KeyError) as exc:
            msg = f"Failed to deserialize artifact {artifact_id!r}"
            logger.warning(
                PERSISTENCE_ARTIFACT_DESERIALIZE_FAILED,
                artifact_id=artifact_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        logger.debug(PERSISTENCE_ARTIFACT_FETCHED, artifact_id=artifact_id, found=True)
        return artifact

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Artifact, ...]:
        """List all artifacts with pagination (no filters).

        Returns:
            The matching entities.
        """
        return await self.query(
            ArtifactFilterSpec(),
            limit=limit,
            offset=offset,
        )

    async def query(
        self,
        filter_spec: ArtifactFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Artifact, ...]:
        """List artifacts with optional filters (paginated).

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        if limit < 1:
            msg = f"limit must be >= 1, got {limit}"
            logger.warning(
                PERSISTENCE_ARTIFACT_LIST_FAILED,
                error=msg,
                param="limit",
                value=limit,
            )
            raise QueryError(msg)
        if offset < 0:
            msg = f"offset must be >= 0, got {offset}"
            logger.warning(
                PERSISTENCE_ARTIFACT_LIST_FAILED,
                error=msg,
                param="offset",
                value=offset,
            )
            raise QueryError(msg)
        effective_limit = min(limit, _MAX_LIST_ROWS)
        conditions: list[str] = []
        params: list[object] = []

        if filter_spec.task_id is not None:
            conditions.append("task_id = %s")
            params.append(filter_spec.task_id)
        if filter_spec.created_by is not None:
            conditions.append("created_by = %s")
            params.append(filter_spec.created_by)
        if filter_spec.artifact_type is not None:
            conditions.append("type = %s")
            params.append(filter_spec.artifact_type.value)

        query = (
            "SELECT id, type, path, task_id, created_by, "
            "description, content_type, size_bytes, created_at, "
            "project_id "
            "FROM artifacts"
        )
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY id LIMIT %s OFFSET %s"
        params.extend([effective_limit, offset])

        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(query, params)
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to list artifacts"
            logger.warning(
                PERSISTENCE_ARTIFACT_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        try:
            artifacts = tuple(Artifact.model_validate(row) for row in rows)
        except (ValueError, ValidationError, KeyError) as exc:
            msg = "Failed to deserialize artifacts"
            logger.warning(
                PERSISTENCE_ARTIFACT_DESERIALIZE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        logger.debug(PERSISTENCE_ARTIFACT_LISTED, count=len(artifacts))
        return artifacts

    async def count(self, filter_spec: ArtifactFilterSpec) -> int:
        """Count artifacts matching the filter spec.

        Returns:
            Number of matching rows.

        Raises:
            QueryError: If the database query fails.
        """
        conditions: list[str] = []
        params: list[object] = []

        if filter_spec.task_id is not None:
            conditions.append("task_id = %s")
            params.append(filter_spec.task_id)
        if filter_spec.created_by is not None:
            conditions.append("created_by = %s")
            params.append(filter_spec.created_by)
        if filter_spec.artifact_type is not None:
            conditions.append("type = %s")
            params.append(filter_spec.artifact_type.value)

        query = "SELECT COUNT(*) FROM artifacts"
        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor() as cur,
            ):
                await cur.execute(query, params)
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = "Failed to count artifacts"
            logger.warning(
                PERSISTENCE_ARTIFACT_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        return row[0] if row else 0

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete an artifact by primary key.

        Args:
            entity_id: Unique artifact identifier.

        Returns:
            ``True`` if a row was deleted, ``False`` if not found.

        Raises:
            QueryError: If the database operation fails.
        """
        artifact_id = entity_id
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM artifacts WHERE id = %s",
                    (artifact_id,),
                )
                deleted = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to delete artifact {artifact_id!r}"
            logger.warning(
                PERSISTENCE_ARTIFACT_DELETE_FAILED,
                artifact_id=artifact_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        return deleted
