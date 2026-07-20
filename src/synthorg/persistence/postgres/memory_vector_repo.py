# module-kind: repository
"""Postgres agent-memory repository with hybrid dense + lexical retrieval.

Sibling of :class:`SQLiteMemoryVectorRepository`, backed by
``psycopg_pool.AsyncConnectionPool`` and pgvector. Ranking is shared: both
backends score lexical hits through :mod:`synthorg.memory.bm25`, so the
two differ only in how rows are fetched, never in how they are ordered.
"""

import json
from datetime import datetime
from typing import Any, Final, LiteralString, NoReturn, cast

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

import synthorg.persistence.postgres._memory_vector_sql as sql
from synthorg.core.memory_enums import MemoryCategory
from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.memory.bm25 import term_frequencies
from synthorg.memory.models import MemoryEntry
from synthorg.memory.vector_spec import MemoryVectorSearchSpec
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import (
    MEMORY_DENSE_INDEX_READY,
    MEMORY_DENSE_INDEX_SCAN_FAILED,
    MEMORY_DENSE_INDEX_UNAVAILABLE,
    MEMORY_DENSE_INDEX_WIDTH_CHANGED,
    MEMORY_ENTRY_COUNT_FAILED,
    MEMORY_ENTRY_DELETE_FAILED,
    MEMORY_ENTRY_RETRIEVAL_FAILED,
    MEMORY_ENTRY_STORE_FAILED,
)
from synthorg.persistence.postgres._memory_vector_rows import rank_lexical, row_to_entry

logger = get_logger(__name__)

# pgvector's <-> returns L2 distance. 1/(1+d) maps it into the bounded
# [0, 1] range MemoryEntry.relevance_score requires, monotonically
# decreasing in distance, matching the SQLite arm exactly.
_DISTANCE_TO_SCORE_OFFSET: Final[float] = 1.0


class PostgresMemoryVectorRepository:
    """Postgres-backed durable agent memory.

    The embedding width arrives at :meth:`ensure_ready` rather than here
    because persistence constructs this repository long before the
    embedder is resolved, and it has no business knowing about embedders.

    Args:
        pool: An open async connection pool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool
        self._dimensions: int | None = None
        self._dense_ready = False

    @property
    def supports_dense_search(self) -> bool:
        """Whether the pgvector column and index are usable."""
        return self._dense_ready

    @property
    def _vector_column(self) -> LiteralString:
        """Dimension-suffixed name of the dense column."""
        return sql.vector_column(self._dimensions or 0)

    async def ensure_ready(self, dimensions: int | None = None) -> None:
        """Add the dense column and its HNSW index for *dimensions*.

        Never raises: a pgvector extension that is absent degrades recall
        rather than taking down persistence for every other feature. The
        capability is reported through :attr:`supports_dense_search` so
        the memory backend fails loud at its own boundary instead.

        Args:
            dimensions: Embedding width, or ``None`` when no embedder is
                wired, in which case recall stays lexical-only.
        """
        if dimensions is not None:
            self._dimensions = dimensions
        if self._dimensions is None or self._dense_ready:
            return
        try:
            async with self._pool.connection() as conn:
                await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
                await conn.execute(
                    sql.add_vector_column(self._vector_column, self._dimensions)
                )
                await conn.execute(sql.create_vector_index(self._vector_column))
        except psycopg.Error as exc:
            logger.warning(
                MEMORY_DENSE_INDEX_UNAVAILABLE,
                dimensions=self._dimensions,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return
        self._dense_ready = True
        logger.info(MEMORY_DENSE_INDEX_READY, dimensions=self._dimensions)
        await self._report_orphaned_widths()

    async def _report_orphaned_widths(self) -> None:
        """Log every dense column left populated at a different width.

        Best-effort: this is a diagnostic, so a failure to look must not
        cost the caller a working dense index.
        """
        try:
            async with self._pool.connection() as conn:
                cursor = await conn.execute(
                    sql.SELECT_VECTOR_COLUMNS, (self._vector_column,)
                )
                stale = [str(row[0]) for row in await cursor.fetchall()]
                for column in stale:
                    # mypy erases LiteralString to str and calls this
                    # redundant; pyright needs it for psycopg's query types.
                    literal = cast("LiteralString", column)  # type: ignore[redundant-cast]
                    cursor = await conn.execute(sql.count_vectors(literal))
                    row = await cursor.fetchone()
                    orphaned = int(row[0]) if row is not None else 0
                    if orphaned:
                        logger.error(
                            MEMORY_DENSE_INDEX_WIDTH_CHANGED,
                            dimensions=self._dimensions,
                            previous_index=column,
                            orphaned_vectors=orphaned,
                        )
        except psycopg.Error as exc:
            logger.warning(
                MEMORY_DENSE_INDEX_SCAN_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def upsert(
        self,
        entry: MemoryEntry,
        /,
        *,
        embedding: tuple[float, ...] | None,
    ) -> None:
        """Insert or replace an entry, its terms and its vector.

        Raises:
            QueryError: If the write fails.
        """
        frequencies = term_frequencies(entry.content)
        params = (
            entry.id,
            entry.agent_id,
            entry.namespace,
            entry.category.value,
            entry.content,
            entry.metadata.source,
            float(entry.metadata.confidence),
            json.dumps(list(entry.metadata.tags)),
            entry.created_at,
            entry.updated_at,
            entry.expires_at,
            sum(frequencies.values()),
        )
        try:
            async with self._pool.connection() as conn, conn.transaction():
                await conn.execute(sql.UPSERT_ENTRY, params)
                await conn.execute(sql.DELETE_TERMS, (entry.id,))
                for term, count in frequencies.items():
                    await conn.execute(sql.INSERT_TERM, (entry.id, term, count))
                if embedding is not None and self._dense_ready:
                    await conn.execute(
                        sql.set_vector(self._vector_column),
                        (sql.encode_vector(embedding), entry.id),
                    )
        except psycopg.Error as exc:
            self._fail("upsert", MEMORY_ENTRY_STORE_FAILED, exc, memory_id=entry.id)

    async def get(
        self,
        agent_id: NotBlankStr,
        memory_id: NotBlankStr,
        /,
    ) -> MemoryEntry | None:
        """Read one entry scoped to its owning agent.

        Returns:
            The entry, or ``None`` when absent or owned by another agent.

        Raises:
            QueryError: If the query fails.
        """
        try:
            async with self._pool.connection() as conn:
                cursor = await conn.cursor(row_factory=dict_row).execute(
                    sql.SELECT_BY_ID, (memory_id, agent_id)
                )
                row = await cursor.fetchone()
        except psycopg.Error as exc:
            self._fail("get", MEMORY_ENTRY_RETRIEVAL_FAILED, exc, memory_id=memory_id)
        return row_to_entry(row) if row is not None else None

    async def delete(
        self,
        agent_id: NotBlankStr,
        memory_id: NotBlankStr,
        /,
    ) -> bool:
        """Delete one entry; terms cascade.

        Returns:
            ``True`` when a row was removed.

        Raises:
            QueryError: If the delete fails.
        """
        try:
            async with self._pool.connection() as conn:
                cursor = await conn.execute(sql.DELETE_BY_ID, (memory_id, agent_id))
                deleted = cursor.rowcount
        except psycopg.Error as exc:
            self._fail("delete", MEMORY_ENTRY_DELETE_FAILED, exc, memory_id=memory_id)
        return deleted > 0

    async def search_dense(
        self,
        spec: MemoryVectorSearchSpec,
        /,
    ) -> tuple[MemoryEntry, ...]:
        """Rank entries by vector similarity.

        Returns:
            Entries in descending similarity order, empty when dense
            search is unavailable or no embedding was supplied.

        Raises:
            QueryError: If the query fails.
        """
        if spec.embedding is None or not self._dense_ready:
            return ()
        where, params = sql.build_filter_clause(spec)
        try:
            async with self._pool.connection() as conn:
                cursor = await conn.cursor(row_factory=dict_row).execute(
                    sql.dense_match(self._vector_column, where),
                    (sql.encode_vector(spec.embedding), *params, spec.limit),
                )
                rows = await cursor.fetchall()
        except psycopg.Error as exc:
            self._fail("search_dense", MEMORY_ENTRY_RETRIEVAL_FAILED, exc)
        return tuple(
            row_to_entry(
                row,
                relevance_score=_DISTANCE_TO_SCORE_OFFSET
                / (_DISTANCE_TO_SCORE_OFFSET + float(row["distance"])),
            )
            for row in rows
        )

    async def search_lexical(
        self,
        spec: MemoryVectorSearchSpec,
        /,
    ) -> tuple[MemoryEntry, ...]:
        """Rank entries by BM25 over the inverted index.

        Returns:
            Entries in descending BM25 order.

        Raises:
            QueryError: If the query fails.
        """
        if spec.text is None:
            return ()
        terms = list(term_frequencies(spec.text))
        if not terms:
            return ()
        where, params = sql.build_filter_clause(spec)
        try:
            async with self._pool.connection() as conn:
                rows = await self._fetch(
                    conn, sql.lexical_postings(where), (terms, *params)
                )
                if not rows:
                    return ()
                stats_rows = await self._fetch(
                    conn, sql.corpus_stats(where), tuple(params)
                )
                frequency_rows = await self._fetch(
                    conn, sql.document_frequency(where), (terms, *params)
                )
        except psycopg.Error as exc:
            self._fail("search_lexical", MEMORY_ENTRY_RETRIEVAL_FAILED, exc)
        stats = stats_rows[0] if stats_rows else None
        return rank_lexical(rows, stats, frequency_rows, limit=spec.limit)

    @staticmethod
    async def _fetch(  # type: ignore[explicit-any]  # pool hands back a row-generic connection
        conn: psycopg.AsyncConnection[Any],
        statement: LiteralString,
        params: tuple[object, ...],
    ) -> list[DictRow]:
        """Run a read and return every row as a dict.

        Returns:
            The fetched rows.
        """
        cursor = await conn.cursor(row_factory=dict_row).execute(statement, params)
        return list(await cursor.fetchall())

    async def list_filtered(  # lint-allow: list-pagination -- spec.limit bounds it
        self,
        spec: MemoryVectorSearchSpec,
        /,
    ) -> tuple[MemoryEntry, ...]:
        """Return filter-matching entries, newest first.

        Returns:
            Matching entries.

        Raises:
            QueryError: If the query fails.
        """
        where, params = sql.build_filter_clause(spec)
        try:
            async with self._pool.connection() as conn:
                rows = await self._fetch(
                    conn, sql.list_filtered(where), (*params, spec.limit)
                )
        except psycopg.Error as exc:
            self._fail("list_filtered", MEMORY_ENTRY_RETRIEVAL_FAILED, exc)
        return tuple(row_to_entry(row) for row in rows)

    async def count(
        self,
        agent_id: NotBlankStr,
        /,
        *,
        category: MemoryCategory | None = None,
    ) -> int:
        """Count an agent's entries.

        Returns:
            The number of matching entries.

        Raises:
            QueryError: If the query fails.
        """
        statement = (
            sql.COUNT_BY_AGENT if category is None else sql.COUNT_BY_AGENT_CATEGORY
        )
        params = (agent_id,) if category is None else (agent_id, category.value)
        try:
            async with self._pool.connection() as conn:
                rows = await self._fetch(conn, statement, params)
        except psycopg.Error as exc:
            self._fail("count", MEMORY_ENTRY_COUNT_FAILED, exc, agent_id=agent_id)
        return int(rows[0]["n"]) if rows else 0

    async def purge_expired(self, now: datetime, /) -> int:
        """Delete every entry expiring at or before ``now``.

        Returns:
            The number of rows deleted.

        Raises:
            QueryError: If the delete fails.
        """
        try:
            async with self._pool.connection() as conn:
                cursor = await conn.execute(sql.DELETE_EXPIRED, (now,))
                deleted = cursor.rowcount
        except psycopg.Error as exc:
            self._fail("purge_expired", MEMORY_ENTRY_DELETE_FAILED, exc)
        return max(deleted, 0)

    async def oldest_ids(
        self,
        agent_id: NotBlankStr,
        /,
        *,
        excess: int,
    ) -> tuple[NotBlankStr, ...]:
        """Return the oldest ``excess`` entry ids for an agent.

        Returns:
            Entry ids, oldest first.

        Raises:
            QueryError: If the query fails.
        """
        if excess <= 0:
            return ()
        try:
            async with self._pool.connection() as conn:
                rows = await self._fetch(
                    conn, sql.SELECT_OLDEST_IDS, (agent_id, excess)
                )
        except psycopg.Error as exc:
            self._fail(
                "oldest_ids", MEMORY_ENTRY_RETRIEVAL_FAILED, exc, agent_id=agent_id
            )
        return tuple(NotBlankStr(str(row["memory_id"])) for row in rows)

    def _fail(
        self,
        operation: str,
        event: str,
        exc: BaseException,
        **context: object,
    ) -> NoReturn:
        """Log a failed statement and raise the typed error.

        Raises:
            QueryError: Always; this is the failure path itself.
        """
        msg = (
            f"Memory vector repository {operation} failed: "
            f"{type(exc).__name__} ({safe_error_description(exc)})"
        )
        logger.warning(
            event,
            operation=operation,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            **context,
        )
        raise QueryError(msg) from exc


__all__ = ["PostgresMemoryVectorRepository"]
