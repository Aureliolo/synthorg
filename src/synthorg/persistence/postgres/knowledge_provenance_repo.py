"""Postgres repository implementation for :class:`ChunkProvenanceRow`."""

import json
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.rows import dict_row
from pydantic import TypeAdapter, ValidationError

from synthorg.core.enums import ContentKind
from synthorg.core.persistence_errors import QueryError
from synthorg.knowledge.models import ChunkProvenanceRow, ProvenanceLocator
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_KNOWLEDGE_PROVENANCE_COUNT_FAILED,
    PERSISTENCE_KNOWLEDGE_PROVENANCE_COUNTED,
    PERSISTENCE_KNOWLEDGE_PROVENANCE_DELETE_FAILED,
    PERSISTENCE_KNOWLEDGE_PROVENANCE_DESERIALIZE_FAILED,
    PERSISTENCE_KNOWLEDGE_PROVENANCE_FETCH_FAILED,
    PERSISTENCE_KNOWLEDGE_PROVENANCE_FETCHED,
    PERSISTENCE_KNOWLEDGE_PROVENANCE_LIST_FAILED,
    PERSISTENCE_KNOWLEDGE_PROVENANCE_LISTED,
    PERSISTENCE_KNOWLEDGE_PROVENANCE_QUERIED,
    PERSISTENCE_KNOWLEDGE_PROVENANCE_QUERY_FAILED,
    PERSISTENCE_KNOWLEDGE_PROVENANCE_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import coerce_row_timestamp
from synthorg.persistence._shared.pagination import validate_pagination_args

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

    from synthorg.core.types import NotBlankStr
    from synthorg.persistence.knowledge_protocol import (
        ChunkProvenanceFilter,
        ChunkProvenanceKey,
    )

logger = get_logger(__name__)

_MAX_LIST_ROWS: int = 100_000
_LOCATOR_ADAPTER: TypeAdapter[ProvenanceLocator] = TypeAdapter(ProvenanceLocator)


def _row_to_provenance(row: dict[str, Any]) -> ChunkProvenanceRow:
    """Reconstruct a :class:`ChunkProvenanceRow` from a ``dict_row``.

    Returns:
        Result of type ``ChunkProvenanceRow``.
    """
    raw_locator = row["locator_json"]
    if isinstance(raw_locator, str):
        raw_locator = json.loads(raw_locator)
    locator = _LOCATOR_ADAPTER.validate_python(raw_locator)
    return ChunkProvenanceRow(
        chunk_id=row["chunk_id"],
        source_id=row["source_id"],
        content_kind=ContentKind(row["content_kind"]),
        chunk_index=row["chunk_index"],
        content_hash=row["content_hash"],
        locator=locator,
        created_at=coerce_row_timestamp(row["created_at"]),
    )


class PostgresChunkProvenanceRepository:
    """Postgres-backed chunk-provenance repository."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    @staticmethod
    def _row_params(entity: ChunkProvenanceRow) -> tuple[object, ...]:
        """Row params.

        Returns:
            Tuple of scalar SQL parameter values for INSERT/UPDATE.
        """
        return (
            entity.chunk_id,
            entity.source_id,
            entity.content_kind.value,
            entity.chunk_index,
            entity.content_hash,
            entity.locator.locator_kind,
            json.dumps(entity.locator.model_dump(mode="json"), sort_keys=True),
            entity.created_at,
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

    async def save(self, entity: ChunkProvenanceRow) -> None:
        """Persist a provenance row via upsert (PK ``chunk_id``).

        Raises:
            QueryError: If the database query fails.
        """
        async with self._pool.connection() as conn, conn.cursor() as cur:
            try:
                await cur.execute(
                    """
                    INSERT INTO knowledge_chunk_provenance (
                        chunk_id, source_id, content_kind, chunk_index,
                        content_hash, locator_kind, locator_json, created_at
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT(chunk_id) DO UPDATE SET
                        source_id=EXCLUDED.source_id,
                        content_kind=EXCLUDED.content_kind,
                        chunk_index=EXCLUDED.chunk_index,
                        content_hash=EXCLUDED.content_hash,
                        locator_kind=EXCLUDED.locator_kind,
                        locator_json=EXCLUDED.locator_json,
                        created_at=EXCLUDED.created_at
                    """,
                    self._row_params(entity),
                )
                await conn.commit()
            except psycopg.Error as exc:
                await self._safe_rollback(
                    conn, event=PERSISTENCE_KNOWLEDGE_PROVENANCE_SAVE_FAILED
                )
                msg = f"Failed to save chunk provenance {entity.chunk_id!r}"
                logger.warning(
                    PERSISTENCE_KNOWLEDGE_PROVENANCE_SAVE_FAILED,
                    chunk_id=entity.chunk_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def get(self, entity_id: ChunkProvenanceKey) -> ChunkProvenanceRow | None:
        """Retrieve a provenance row by ``chunk_id``.

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
                    "SELECT * FROM knowledge_chunk_provenance WHERE chunk_id = %s",
                    (entity_id,),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Failed to fetch chunk provenance {entity_id!r}"
            logger.warning(
                PERSISTENCE_KNOWLEDGE_PROVENANCE_FETCH_FAILED,
                chunk_id=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            logger.debug(
                PERSISTENCE_KNOWLEDGE_PROVENANCE_FETCHED,
                chunk_id=entity_id,
                found=False,
            )
            return None
        return self._rows_to_tuple((row,))[0]

    async def get_many(
        self,
        chunk_ids: tuple[ChunkProvenanceKey, ...],
    ) -> tuple[ChunkProvenanceRow, ...]:
        """Fetch many provenance rows by id in one round trip (ADR-0001 D7).

        Returns:
            Tuple of matching rows; empty when no rows match.

        Raises:
            QueryError: If the database query fails.
        """
        if not chunk_ids:
            return ()
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT * FROM knowledge_chunk_provenance WHERE chunk_id = ANY(%s)",
                    (list(chunk_ids),),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to fetch chunk provenance batch"
            logger.warning(
                PERSISTENCE_KNOWLEDGE_PROVENANCE_FETCH_FAILED,
                count=len(chunk_ids),
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
    ) -> tuple[ChunkProvenanceRow, ...]:
        """List provenance rows ordered by ``(source_id, chunk_index)``.

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_KNOWLEDGE_PROVENANCE_LIST_FAILED
        )
        effective_limit = min(limit, _MAX_LIST_ROWS)
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    """SELECT * FROM knowledge_chunk_provenance
                       ORDER BY source_id ASC, chunk_index ASC
                       LIMIT %s OFFSET %s""",
                    (effective_limit, offset),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to list chunk provenance"
            logger.warning(
                PERSISTENCE_KNOWLEDGE_PROVENANCE_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return self._rows_to_tuple(tuple(rows))

    async def delete(self, entity_id: ChunkProvenanceKey) -> bool:
        """Delete a provenance row by ``chunk_id``.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        async with self._pool.connection() as conn, conn.cursor() as cur:
            try:
                await cur.execute(
                    "DELETE FROM knowledge_chunk_provenance WHERE chunk_id = %s",
                    (entity_id,),
                )
                deleted = cur.rowcount > 0
                await conn.commit()
            except psycopg.Error as exc:
                await self._safe_rollback(
                    conn, event=PERSISTENCE_KNOWLEDGE_PROVENANCE_DELETE_FAILED
                )
                msg = f"Failed to delete chunk provenance {entity_id!r}"
                logger.warning(
                    PERSISTENCE_KNOWLEDGE_PROVENANCE_DELETE_FAILED,
                    chunk_id=entity_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
            return deleted

    async def delete_by_source(self, source_id: NotBlankStr) -> int:
        """Delete every provenance row for a source (ADR-0001 D7).

        Returns:
            Number of rows deleted.

        Raises:
            QueryError: If the database query fails.
        """
        async with self._pool.connection() as conn, conn.cursor() as cur:
            try:
                await cur.execute(
                    "DELETE FROM knowledge_chunk_provenance WHERE source_id = %s",
                    (source_id,),
                )
                removed = cur.rowcount
                await conn.commit()
            except psycopg.Error as exc:
                await self._safe_rollback(
                    conn, event=PERSISTENCE_KNOWLEDGE_PROVENANCE_DELETE_FAILED
                )
                msg = f"Failed to delete chunk provenance for source {source_id!r}"
                logger.warning(
                    PERSISTENCE_KNOWLEDGE_PROVENANCE_DELETE_FAILED,
                    source_id=source_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
            return removed

    async def query(
        self,
        filter_spec: ChunkProvenanceFilter,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ChunkProvenanceRow, ...]:
        """Return provenance rows for a source, ``chunk_index`` ascending.

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_KNOWLEDGE_PROVENANCE_QUERY_FAILED
        )
        effective_limit = min(limit, _MAX_LIST_ROWS)
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    """SELECT * FROM knowledge_chunk_provenance
                       WHERE source_id = %s
                       ORDER BY chunk_index ASC
                       LIMIT %s OFFSET %s""",
                    (filter_spec.source_id, effective_limit, offset),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = f"Provenance query failed for source {filter_spec.source_id!r}"
            logger.warning(
                PERSISTENCE_KNOWLEDGE_PROVENANCE_QUERY_FAILED,
                source_id=filter_spec.source_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        rows_out = self._rows_to_tuple(tuple(rows))
        logger.debug(
            PERSISTENCE_KNOWLEDGE_PROVENANCE_QUERIED,
            source_id=filter_spec.source_id,
            count=len(rows_out),
        )
        return rows_out

    async def count(self, filter_spec: ChunkProvenanceFilter) -> int:
        """Count provenance rows for a source.

        Returns:
            Number of matching rows.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT COUNT(*) AS n FROM knowledge_chunk_provenance "
                    "WHERE source_id = %s",
                    (filter_spec.source_id,),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Provenance count failed for source {filter_spec.source_id!r}"
            logger.warning(
                PERSISTENCE_KNOWLEDGE_PROVENANCE_COUNT_FAILED,
                source_id=filter_spec.source_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        count = int(row["n"]) if row is not None else 0
        logger.debug(
            PERSISTENCE_KNOWLEDGE_PROVENANCE_COUNTED,
            source_id=filter_spec.source_id,
            count=count,
        )
        return count

    def _rows_to_tuple(
        self, rows: tuple[dict[str, Any], ...]
    ) -> tuple[ChunkProvenanceRow, ...]:
        """Deserialise a row batch with one shared error path.

        Returns:
            The matching collection.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            provenance = tuple(_row_to_provenance(row) for row in rows)
        except (ValueError, ValidationError, KeyError, json.JSONDecodeError) as exc:
            msg = "Failed to deserialize chunk provenance"
            logger.warning(
                PERSISTENCE_KNOWLEDGE_PROVENANCE_DESERIALIZE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_KNOWLEDGE_PROVENANCE_LISTED, count=len(provenance))
        return provenance
