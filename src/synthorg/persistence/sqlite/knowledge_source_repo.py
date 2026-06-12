"""SQLite repository implementation for :class:`KnowledgeSource`.

Persists the knowledge-source registry. Chunk text lives in the memory
backend; this row carries source identity, scope, content hash, and
ingestion status. ``project_id`` is nullable (NULL means global).
"""

import sqlite3
from collections.abc import Iterable

import aiosqlite
from pydantic import ValidationError

from synthorg.core.persistence_errors import QueryError
from synthorg.knowledge.enums import SourceStatus, SourceType
from synthorg.knowledge.models import KnowledgeSource
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.knowledge_source import (
    PERSISTENCE_KNOWLEDGE_SOURCE_COUNT_FAILED,
    PERSISTENCE_KNOWLEDGE_SOURCE_COUNTED,
    PERSISTENCE_KNOWLEDGE_SOURCE_DELETE_FAILED,
    PERSISTENCE_KNOWLEDGE_SOURCE_DESERIALIZE_FAILED,
    PERSISTENCE_KNOWLEDGE_SOURCE_FETCH_FAILED,
    PERSISTENCE_KNOWLEDGE_SOURCE_FETCHED,
    PERSISTENCE_KNOWLEDGE_SOURCE_LIST_FAILED,
    PERSISTENCE_KNOWLEDGE_SOURCE_LISTED,
    PERSISTENCE_KNOWLEDGE_SOURCE_QUERIED,
    PERSISTENCE_KNOWLEDGE_SOURCE_QUERY_FAILED,
    PERSISTENCE_KNOWLEDGE_SOURCE_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import coerce_row_timestamp, format_iso_utc
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.knowledge_protocol import (
    KnowledgeSourceFilter,
    KnowledgeSourceKey,
)
from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)

_MAX_LIST_ROWS: int = 10_000


def _row_to_source(row: aiosqlite.Row) -> KnowledgeSource:
    """Reconstruct a :class:`KnowledgeSource` from a database row.

    Returns:
        Result of type ``KnowledgeSource``.
    """
    data = dict(row)
    data["source_type"] = SourceType(data["source_type"])
    data["status"] = SourceStatus(data["status"])
    data["created_at"] = coerce_row_timestamp(data["created_at"])
    data["updated_at"] = coerce_row_timestamp(data["updated_at"])
    if data["last_indexed_at"] is not None:
        data["last_indexed_at"] = coerce_row_timestamp(data["last_indexed_at"])
    return KnowledgeSource.model_validate(data)


class SQLiteKnowledgeSourceRepository:
    """SQLite-backed knowledge-source registry repository."""

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._write_context = write_context

    @staticmethod
    def _row_params(entity: KnowledgeSource) -> tuple[object, ...]:
        """Row params.

        Returns:
            Tuple of scalar SQL parameter values for INSERT/UPDATE.
        """
        return (
            entity.source_id,
            entity.source_type.value,
            entity.project_id,
            entity.uri,
            entity.title,
            entity.content_hash,
            entity.status.value,
            entity.chunk_count,
            format_iso_utc(entity.created_at),
            format_iso_utc(entity.updated_at),
            format_iso_utc(entity.last_indexed_at)
            if entity.last_indexed_at is not None
            else None,
            entity.last_error,
        )

    async def _safe_rollback(self, *, event: str) -> None:
        """Safe rollback."""
        try:
            await self._db.rollback()
        except (sqlite3.Error, aiosqlite.Error) as rollback_exc:
            logger.warning(
                event,
                error_type=type(rollback_exc).__name__,
                error=safe_error_description(rollback_exc),
                rollback_failed=True,
            )

    async def save(self, entity: KnowledgeSource) -> None:
        """Persist a source row via upsert (PK ``source_id``).

        Raises:
            QueryError: If the database query fails.
        """
        async with self._write_context():
            try:
                await self._db.execute(
                    """\
INSERT INTO knowledge_sources (source_id, source_type, project_id, uri, title,
                              content_hash, status, chunk_count,
                              created_at, updated_at, last_indexed_at, last_error)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(source_id) DO UPDATE SET
    source_type=excluded.source_type,
    project_id=excluded.project_id,
    uri=excluded.uri,
    title=excluded.title,
    content_hash=excluded.content_hash,
    status=excluded.status,
    chunk_count=excluded.chunk_count,
    created_at=excluded.created_at,
    updated_at=excluded.updated_at,
    last_indexed_at=excluded.last_indexed_at,
    last_error=excluded.last_error""",
                    self._row_params(entity),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback(
                    event=PERSISTENCE_KNOWLEDGE_SOURCE_SAVE_FAILED
                )
                msg = f"Failed to save knowledge source {entity.source_id!r}"
                logger.warning(
                    PERSISTENCE_KNOWLEDGE_SOURCE_SAVE_FAILED,
                    source_id=entity.source_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def get(self, entity_id: KnowledgeSourceKey) -> KnowledgeSource | None:
        """Retrieve a source by ``source_id``.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            cursor = await self._db.execute(
                "SELECT * FROM knowledge_sources WHERE source_id = ?",
                (entity_id,),
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to fetch knowledge source {entity_id!r}"
            logger.warning(
                PERSISTENCE_KNOWLEDGE_SOURCE_FETCH_FAILED,
                source_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            logger.debug(
                PERSISTENCE_KNOWLEDGE_SOURCE_FETCHED, source_id=entity_id, found=False
            )
            return None
        source = self._rows_to_tuple((row,))[0]
        logger.debug(
            PERSISTENCE_KNOWLEDGE_SOURCE_FETCHED, source_id=entity_id, found=True
        )
        return source

    async def get_many(
        self,
        source_ids: tuple[KnowledgeSourceKey, ...],
    ) -> tuple[KnowledgeSource, ...]:
        """Fetch many sources by id in one round trip (ADR-0001 D7).

        Returns:
            The rows that exist, in unspecified order; missing ids are omitted.

        Raises:
            QueryError: If the database query fails.
        """
        if not source_ids:
            return ()
        placeholders = ",".join("?" for _ in source_ids)
        try:
            async with self._db.execute(
                "SELECT * FROM knowledge_sources "  # noqa: S608 -- placeholders are bound params, not interpolated values
                f"WHERE source_id IN ({placeholders})",
                tuple(source_ids),
            ) as cursor:
                rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to fetch knowledge sources by id"
            logger.warning(
                PERSISTENCE_KNOWLEDGE_SOURCE_FETCH_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return self._rows_to_tuple(tuple(rows))

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[KnowledgeSource, ...]:
        """List all sources, most-recent first.

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_KNOWLEDGE_SOURCE_LIST_FAILED
        )
        effective_limit = min(limit, _MAX_LIST_ROWS)
        try:
            cursor = await self._db.execute(
                """SELECT * FROM knowledge_sources
                   ORDER BY updated_at DESC, source_id DESC
                   LIMIT ? OFFSET ?""",
                (effective_limit, offset),
            )
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to list knowledge sources"
            logger.warning(
                PERSISTENCE_KNOWLEDGE_SOURCE_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return self._rows_to_tuple(tuple(rows))

    async def delete(self, entity_id: KnowledgeSourceKey) -> bool:
        """Delete a source by ``source_id``.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "DELETE FROM knowledge_sources WHERE source_id = ?",
                    (entity_id,),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback(
                    event=PERSISTENCE_KNOWLEDGE_SOURCE_DELETE_FAILED
                )
                msg = f"Failed to delete knowledge source {entity_id!r}"
                logger.warning(
                    PERSISTENCE_KNOWLEDGE_SOURCE_DELETE_FAILED,
                    source_id=entity_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
            return cursor.rowcount > 0

    async def query(
        self,
        filter_spec: KnowledgeSourceFilter,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[KnowledgeSource, ...]:
        """Return sources matching the filter, most-recent first.

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_KNOWLEDGE_SOURCE_QUERY_FAILED
        )
        effective_limit = min(limit, _MAX_LIST_ROWS)
        where_sql, params = _build_query_sql(filter_spec)
        sql = (
            f"SELECT * {where_sql} "
            "ORDER BY updated_at DESC, source_id DESC LIMIT ? OFFSET ?"
        )
        params = (*params, effective_limit, offset)
        try:
            cursor = await self._db.execute(sql, params)
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to query knowledge sources"
            logger.warning(
                PERSISTENCE_KNOWLEDGE_SOURCE_QUERY_FAILED,
                project_id=filter_spec.project_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        sources = self._rows_to_tuple(tuple(rows))
        logger.debug(
            PERSISTENCE_KNOWLEDGE_SOURCE_QUERIED,
            project_id=filter_spec.project_id,
            count=len(sources),
        )
        return sources

    async def count(self, filter_spec: KnowledgeSourceFilter) -> int:
        """Count sources matching the filter spec.

        Returns:
            Number of matching rows.

        Raises:
            QueryError: If the database query fails.
        """
        where_sql, params = _build_query_sql(filter_spec)
        sql = f"SELECT COUNT(*) AS n {where_sql}"
        try:
            cursor = await self._db.execute(sql, params)
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to count knowledge sources"
            logger.warning(
                PERSISTENCE_KNOWLEDGE_SOURCE_COUNT_FAILED,
                project_id=filter_spec.project_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        count = int(row["n"]) if row is not None else 0
        logger.debug(
            PERSISTENCE_KNOWLEDGE_SOURCE_COUNTED,
            project_id=filter_spec.project_id,
            count=count,
        )
        return count

    def _rows_to_tuple(
        self, rows: Iterable[aiosqlite.Row]
    ) -> tuple[KnowledgeSource, ...]:
        """Deserialise a row batch with one shared error path.

        Returns:
            The matching collection.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            sources = tuple(_row_to_source(row) for row in rows)
        except (ValueError, ValidationError, KeyError) as exc:
            msg = "Failed to deserialize knowledge sources"
            logger.warning(
                PERSISTENCE_KNOWLEDGE_SOURCE_DESERIALIZE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_KNOWLEDGE_SOURCE_LISTED, count=len(sources))
        return sources


def _build_query_sql(
    filter_spec: KnowledgeSourceFilter,
) -> tuple[str, tuple[object, ...]]:
    """Compose the ``FROM ... WHERE`` fragment for ``query`` / ``count``.

    Scope semantics combine ``project_id`` and ``include_global`` exactly
    as documented on :class:`KnowledgeSourceFilter`. The Postgres repo
    has its own ``%s``-placeholder twin of this helper.

    Returns:
        ``(sql, params)`` where ``sql`` is the ``FROM ... WHERE`` fragment (callers
        prepend their own ``SELECT`` clause) and ``params`` is the matching
        positional parameter tuple.
    """
    conditions: list[str] = []
    params: list[object] = []
    pid = filter_spec.project_id
    if pid is not None and filter_spec.include_global:
        conditions.append("(project_id = ? OR project_id IS NULL)")
        params.append(pid)
    elif pid is not None:
        conditions.append("project_id = ?")
        params.append(pid)
    elif filter_spec.include_global:
        conditions.append("project_id IS NULL")
    if filter_spec.source_type is not None:
        conditions.append("source_type = ?")
        params.append(filter_spec.source_type.value)
    if filter_spec.status is not None:
        conditions.append("status = ?")
        params.append(filter_spec.status.value)
    if filter_spec.stale_only:
        conditions.append("status = ?")
        params.append(SourceStatus.STALE.value)
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    return f"FROM knowledge_sources{where}", tuple(params)
