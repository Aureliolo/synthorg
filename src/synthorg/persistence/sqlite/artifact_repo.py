"""SQLite repository implementation for Artifact."""

import sqlite3
from datetime import UTC, datetime

import aiosqlite
from pydantic import ValidationError

from synthorg.core.artifact import Artifact, ArtifactType
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
from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)

_MAX_LIST_ROWS: int = 10_000


def _row_to_artifact(row: aiosqlite.Row) -> Artifact:
    """Reconstruct an ``Artifact`` from a database row.

    Args:
        row: A single database row with artifact columns.

    Returns:
        Validated ``Artifact`` model instance.
    """
    data = dict(row)
    data["type"] = ArtifactType(data["type"])
    raw_ts = data["created_at"]
    if raw_ts is not None:
        parsed = datetime.fromisoformat(raw_ts)
        # Ensure timezone-aware -- stored as UTC ISO string.
        data["created_at"] = (
            parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
        )
    return Artifact.model_validate(data)


class SQLiteArtifactRepository:
    """SQLite-backed artifact repository.

    Provides CRUD operations for ``Artifact`` models using a shared
    ``aiosqlite.Connection``.  All write operations commit immediately.

    Args:
        db: An open aiosqlite connection with ``row_factory``
            set to ``aiosqlite.Row``.
        write_context: Async context manager that serializes writes on
            the shared connection. Supplied by
            ``SQLitePersistenceBackend.write_context`` in production;
            tests can pass
            ``tests._shared.persistence.make_private_write_context()``
            for standalone construction.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._write_context = write_context

    async def _safe_rollback(
        self,
        failure_event: str = PERSISTENCE_ARTIFACT_SAVE_FAILED,
    ) -> None:
        """Best-effort rollback on the shared connection.

        ``SQLiteArtifactRepository`` shares a single
        :class:`aiosqlite.Connection` across requests.  When
        ``execute()`` / ``commit()`` raises mid-write, leaving the
        transaction open lets later calls inherit the failed state or
        keep the DB locked.  Rolling back here clears the per-write
        transaction without disturbing successful sibling repositories
        that share the same connection.

        The rollback itself is wrapped: a secondary failure (e.g. the
        connection is already closed) must not mask the original error
        the caller is propagating.  We DO log the rollback failure so
        a tainted shared connection leaves a trail in observability
        instead of silently degrading later writes.

        Args:
            failure_event: Event constant attributed to the rollback
                failure log.  Defaults to ``PERSISTENCE_ARTIFACT_SAVE_FAILED``
                so existing call sites stay valid; ``delete()`` should
                pass ``PERSISTENCE_ARTIFACT_DELETE_FAILED`` so a
                rollback-after-delete failure is filed against the
                correct operation in dashboards.
        """
        try:
            await self._db.rollback()
        except (sqlite3.Error, aiosqlite.Error) as rollback_exc:
            logger.warning(
                failure_event,
                error_type=type(rollback_exc).__name__,
                error=safe_error_description(rollback_exc),
                rollback_failed=True,
            )

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

        Implements the upsert as ``INSERT ... ON CONFLICT(id) DO
        NOTHING`` followed by a conditional ``UPDATE``, both inside
        one transaction.  The first ``INSERT``'s ``rowcount``
        distinguishes the lifecycle outcome without a TOCTOU
        ``get`` + ``save`` race -- concurrent writers can no longer
        both observe "missing" and both report ``API_ARTIFACT_CREATED``.

        ``ON CONFLICT(id) DO NOTHING`` (not ``INSERT OR IGNORE``)
        narrows conflict resolution to the ``id`` primary key only.
        Other constraint failures -- ``NOT NULL``, ``CHECK``, ``FOREIGN
        KEY`` -- propagate as ``IntegrityError`` so a malformed
        artifact does not silently sail past the insert and then update
        zero rows ("save returned False" without anything actually
        persisted).

        Args:
            artifact: Artifact model to persist.

        Returns:
            ``True`` when this call inserted a new row, ``False`` when
            it updated an existing row in place.

        Raises:
            QueryError: If the database operation fails.
        """
        created_at_iso = (
            artifact.created_at.astimezone(UTC).isoformat()
            if artifact.created_at is not None
            else None
        )
        params = (
            artifact.id,
            artifact.type.value,
            artifact.path,
            artifact.task_id,
            artifact.created_by,
            artifact.description,
            artifact.content_type,
            artifact.size_bytes,
            created_at_iso,
            artifact.project_id,
        )
        async with self._write_context():
            try:
                async with self._db.execute(
                    """\
INSERT INTO artifacts (id, type, path, task_id, created_by,
                       description, content_type, size_bytes,
                       created_at, project_id)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO NOTHING""",
                    params,
                ) as insert_cursor:
                    inserted = insert_cursor.rowcount > 0
                if not inserted:
                    await self._db.execute(
                        """\
UPDATE artifacts SET
    type=?,
    path=?,
    task_id=?,
    created_by=?,
    description=?,
    content_type=?,
    size_bytes=?,
    created_at=?,
    project_id=?
WHERE id=?""",
                        (
                            artifact.type.value,
                            artifact.path,
                            artifact.task_id,
                            artifact.created_by,
                            artifact.description,
                            artifact.content_type,
                            artifact.size_bytes,
                            created_at_iso,
                            artifact.project_id,
                            artifact.id,
                        ),
                    )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback()
                msg = f"Failed to save artifact {artifact.id!r}"
                logger.warning(
                    PERSISTENCE_ARTIFACT_SAVE_FAILED,
                    artifact_id=artifact.id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
            return inserted

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
            async with self._db.execute(
                "SELECT * FROM artifacts WHERE id = ?", (artifact_id,)
            ) as cursor:
                row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
            artifact = _row_to_artifact(row)
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
        # Cap the page size at ``_MAX_LIST_ROWS`` so a runaway caller
        # cannot exhaust SQLite memory with a single oversized fetch.
        effective_limit = min(limit, _MAX_LIST_ROWS)
        # Explicit column list (matches Postgres) so a future schema
        # change does not silently widen the returned shape.
        query = (
            "SELECT id, type, path, task_id, created_by, "
            "description, content_type, size_bytes, created_at, "
            "project_id "
            "FROM artifacts"
        )
        conditions: list[str] = []
        params: list[object] = []

        if filter_spec.task_id is not None:
            conditions.append("task_id = ?")
            params.append(filter_spec.task_id)
        if filter_spec.created_by is not None:
            conditions.append("created_by = ?")
            params.append(filter_spec.created_by)
        if filter_spec.artifact_type is not None:
            conditions.append("type = ?")
            params.append(filter_spec.artifact_type.value)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)
        query += " ORDER BY id LIMIT ? OFFSET ?"
        params.extend([effective_limit, offset])

        try:
            async with self._db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to list artifacts"
            logger.warning(
                PERSISTENCE_ARTIFACT_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        try:
            artifacts = tuple(_row_to_artifact(row) for row in rows)
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
        query = "SELECT COUNT(*) FROM artifacts"
        conditions: list[str] = []
        params: list[object] = []

        if filter_spec.task_id is not None:
            conditions.append("task_id = ?")
            params.append(filter_spec.task_id)
        if filter_spec.created_by is not None:
            conditions.append("created_by = ?")
            params.append(filter_spec.created_by)
        if filter_spec.artifact_type is not None:
            conditions.append("type = ?")
            params.append(filter_spec.artifact_type.value)

        if conditions:
            query += " WHERE " + " AND ".join(conditions)

        try:
            async with self._db.execute(query, params) as cursor:
                row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
        async with self._write_context():
            try:
                async with self._db.execute(
                    "DELETE FROM artifacts WHERE id = ?", (artifact_id,)
                ) as cursor:
                    await self._db.commit()
                    deleted = cursor.rowcount > 0
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback(PERSISTENCE_ARTIFACT_DELETE_FAILED)
                msg = f"Failed to delete artifact {artifact_id!r}"
                logger.warning(
                    PERSISTENCE_ARTIFACT_DELETE_FAILED,
                    artifact_id=artifact_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
            return deleted
