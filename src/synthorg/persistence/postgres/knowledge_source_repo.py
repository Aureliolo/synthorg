"""Postgres repository implementation for :class:`KnowledgeSource`."""

from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.rows import dict_row
from pydantic import ValidationError

from synthorg.core.enums import SourceStatus, SourceType
from synthorg.core.persistence_errors import QueryError
from synthorg.knowledge.models import KnowledgeSource
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
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
from synthorg.persistence._shared import coerce_row_timestamp
from synthorg.persistence._shared.pagination import validate_pagination_args

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

    from synthorg.persistence.knowledge_protocol import (
        KnowledgeSourceFilter,
        KnowledgeSourceKey,
    )

logger = get_logger(__name__)

_MAX_LIST_ROWS: int = 10_000


def _row_to_source(row: dict[str, Any]) -> KnowledgeSource:
    """Reconstruct a :class:`KnowledgeSource` from a Postgres ``dict_row``.

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


class PostgresKnowledgeSourceRepository:
    """Postgres-backed knowledge-source registry repository."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    @staticmethod
    def _row_params(entity: KnowledgeSource) -> tuple[object, ...]:
        """Row params.

        Returns:
            The matching collection.
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
            entity.created_at,
            entity.updated_at,
            entity.last_indexed_at,
            entity.last_error,
        )

    async def _safe_rollback(
        self, conn: psycopg.AsyncConnection[Any], *, event: str
    ) -> None:
        """Safe rollback."""
        try:
            await conn.rollback()
        except psycopg.Error as rollback_exc:
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
        async with self._pool.connection() as conn, conn.cursor() as cur:
            try:
                await cur.execute(
                    """
                    INSERT INTO knowledge_sources (
                        source_id, source_type, project_id, uri, title,
                        content_hash, status, chunk_count,
                        created_at, updated_at, last_indexed_at, last_error
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT(source_id) DO UPDATE SET
                        source_type=EXCLUDED.source_type,
                        project_id=EXCLUDED.project_id,
                        uri=EXCLUDED.uri,
                        title=EXCLUDED.title,
                        content_hash=EXCLUDED.content_hash,
                        status=EXCLUDED.status,
                        chunk_count=EXCLUDED.chunk_count,
                        updated_at=EXCLUDED.updated_at,
                        last_indexed_at=EXCLUDED.last_indexed_at,
                        last_error=EXCLUDED.last_error
                    """,
                    self._row_params(entity),
                )
                await conn.commit()
            except psycopg.Error as exc:
                await self._safe_rollback(
                    conn, event=PERSISTENCE_KNOWLEDGE_SOURCE_SAVE_FAILED
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
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT * FROM knowledge_sources WHERE source_id = %s",
                    (entity_id,),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
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
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    """SELECT * FROM knowledge_sources
                       ORDER BY updated_at DESC, source_id DESC
                       LIMIT %s OFFSET %s""",
                    (effective_limit, offset),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
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
        async with self._pool.connection() as conn, conn.cursor() as cur:
            try:
                await cur.execute(
                    "DELETE FROM knowledge_sources WHERE source_id = %s",
                    (entity_id,),
                )
                deleted = cur.rowcount > 0
                await conn.commit()
            except psycopg.Error as exc:
                await self._safe_rollback(
                    conn, event=PERSISTENCE_KNOWLEDGE_SOURCE_DELETE_FAILED
                )
                msg = f"Failed to delete knowledge source {entity_id!r}"
                logger.warning(
                    PERSISTENCE_KNOWLEDGE_SOURCE_DELETE_FAILED,
                    source_id=entity_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
            return deleted

    async def query(
        self,
        filter_spec: KnowledgeSourceFilter,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[KnowledgeSource, ...]:
        """Return sources matching the filter, most-recent first.

        Returns:
            Tuple of (items, next_cursor) for paginated iteration.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_KNOWLEDGE_SOURCE_QUERY_FAILED
        )
        effective_limit = min(limit, _MAX_LIST_ROWS)
        sql, params = _build_query_sql(filter_spec)
        sql += " ORDER BY updated_at DESC, source_id DESC LIMIT %s OFFSET %s"
        params = (*params, effective_limit, offset)
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, params)
                rows = await cur.fetchall()
        except psycopg.Error as exc:
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
        base_sql, params = _build_query_sql(filter_spec)
        sql = base_sql.replace("SELECT *", "SELECT COUNT(*) AS n", 1)
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, params)
                row = await cur.fetchone()
        except psycopg.Error as exc:
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
        self, rows: tuple[dict[str, Any], ...]
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
    """Compose the ``SELECT * ... WHERE`` clause for ``query`` / ``count``.

    Returns:
        ``(sql, params)`` where ``sql`` is the complete query string and ``params`` is
        the matching positional parameter tuple.
    """
    conditions: list[str] = []
    params: list[object] = []
    pid = filter_spec.project_id
    if pid is not None and filter_spec.include_global:
        conditions.append("(project_id = %s OR project_id IS NULL)")
        params.append(pid)
    elif pid is not None:
        conditions.append("project_id = %s")
        params.append(pid)
    elif filter_spec.include_global:
        conditions.append("project_id IS NULL")
    if filter_spec.source_type is not None:
        conditions.append("source_type = %s")
        params.append(filter_spec.source_type.value)
    if filter_spec.status is not None:
        conditions.append("status = %s")
        params.append(filter_spec.status.value)
    if filter_spec.stale_only:
        conditions.append("status = %s")
        params.append(SourceStatus.STALE.value)
    where = f" WHERE {' AND '.join(conditions)}" if conditions else ""
    # Injection-safe: every fragment in ``conditions`` is a static literal;
    # all user-supplied values travel as bound ``params``, never interpolated.
    return f"SELECT * FROM knowledge_sources{where}", tuple(params)  # noqa: S608
