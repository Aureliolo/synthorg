# module-kind: repository
"""SQLite agent-memory repository with hybrid dense + lexical retrieval.

Satisfies ``MemoryVectorRepository``. Dense retrieval uses ``sqlite-vec``
loaded as a runtime extension; lexical retrieval uses the declared
``memory_entry_terms`` inverted index scored by the shared BM25 code in
:mod:`synthorg.memory.bm25`, so both backends rank identically.

The dense index is a ``vec0`` virtual table named for its dimension.
``vec0`` needs a literal width at creation, and the embedder is
operator-configurable, so it cannot be declared in the migration. Naming
it for the dimension turns an embedder change into a clean re-index
rather than a silent mix of incompatible vectors.
"""

import sqlite3
from datetime import datetime
from typing import Final, NoReturn

import aiosqlite

import synthorg.persistence.sqlite._memory_vector_sql as sql
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
from synthorg.persistence._shared import format_iso_utc
from synthorg.persistence.sqlite._memory_vector_rows import (
    encode_tags,
    pack_embedding,
    rank_lexical,
    row_to_entry,
)
from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)

# vec0 returns squared L2 distance. Mapping it to a bounded score with
# 1/(1+d) keeps the value inside MemoryEntry.relevance_score's [0, 1]
# range and is monotonically decreasing in distance, which is all the
# downstream rank-based fusion needs.
_DISTANCE_TO_SCORE_OFFSET: Final[float] = 1.0


class SQLiteMemoryVectorRepository:
    """SQLite-backed durable agent memory.

    The embedding width arrives at :meth:`ensure_ready` rather than here
    because persistence constructs this repository long before the
    embedder is resolved, and it has no business knowing about embedders.

    Args:
        db: An open aiosqlite connection.
        write_context: Async write-serialising context manager.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._db.row_factory = aiosqlite.Row
        self._write_context = write_context
        self._dimensions: int | None = None
        self._dense_ready = False

    @property
    def supports_dense_search(self) -> bool:
        """Whether the ``vec0`` index is loaded and usable."""
        return self._dense_ready

    @property
    def _vector_table(self) -> str:
        """Dimension-suffixed name of the dense index table."""
        return f"memory_entries_vec_{self._dimensions}"

    async def ensure_ready(self, dimensions: int | None = None) -> None:
        """Load ``sqlite-vec`` and create the dense index for *dimensions*.

        Never raises: a missing extension degrades recall rather than
        taking down persistence, which also serves every non-memory
        feature on this connection. The capability is reported through
        :attr:`supports_dense_search` so the memory backend can fail
        loud at its own boundary instead.

        Args:
            dimensions: Embedding width, or ``None`` when no embedder is
                wired, in which case recall stays lexical-only.
        """
        if dimensions is not None:
            self._dimensions = dimensions
        if self._dimensions is None or self._dense_ready:
            return
        try:
            import sqlite_vec  # noqa: PLC0415 -- optional runtime extension

            # Positional bool is the sqlite3 API's own shape, not ours.
            await self._db.enable_load_extension(True)  # noqa: FBT003
            await self._db.load_extension(sqlite_vec.loadable_path())
            await self._db.enable_load_extension(False)  # noqa: FBT003
            async with self._write_context():
                await self._db.execute(
                    sql.create_vector_table(self._vector_table, self._dimensions)
                )
                await self._db.commit()
        except (ImportError, AttributeError, sqlite3.Error, aiosqlite.Error) as exc:
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
        """Log every dense index left behind at a different width.

        Best-effort: this is a diagnostic, so a failure to look must not
        cost the caller a working dense index.
        """
        try:
            async with self._db.execute(
                sql.SELECT_VECTOR_TABLES, (self._vector_table,)
            ) as cursor:
                stale = [str(row[0]) for row in await cursor.fetchall()]
            for table in stale:
                async with self._db.execute(sql.count_vectors(table)) as cursor:
                    row = await cursor.fetchone()
                orphaned = int(row[0]) if row is not None else 0
                if orphaned:
                    logger.error(
                        MEMORY_DENSE_INDEX_WIDTH_CHANGED,
                        dimensions=self._dimensions,
                        previous_index=table,
                        orphaned_vectors=orphaned,
                    )
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
        """Insert or replace an entry, its inverted-index terms and vector.

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
            encode_tags(entry.metadata.tags),
            format_iso_utc(entry.created_at),
            format_iso_utc(entry.updated_at) if entry.updated_at else None,
            format_iso_utc(entry.expires_at) if entry.expires_at else None,
            sum(frequencies.values()),
        )
        async with self._write_context():
            try:
                await self._db.execute(sql.UPSERT_ENTRY, params)
                await self._db.execute(sql.DELETE_TERMS, (entry.id,))
                await self._db.executemany(
                    sql.INSERT_TERM,
                    [(entry.id, term, count) for term, count in frequencies.items()],
                )
                await self._write_vector(entry.id, embedding)
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._rollback("upsert")
                self._fail("upsert", MEMORY_ENTRY_STORE_FAILED, exc, memory_id=entry.id)

    async def _write_vector(
        self,
        memory_id: NotBlankStr,
        embedding: tuple[float, ...] | None,
    ) -> None:
        """Replace the dense-index row for one entry, if dense is available."""
        if embedding is None or not self._dense_ready:
            return
        row_id = await self._row_id(memory_id)
        if row_id is None:
            return
        await self._db.execute(sql.delete_vector(self._vector_table), (row_id,))
        await self._db.execute(
            sql.upsert_vector(self._vector_table),
            (row_id, pack_embedding(embedding)),
        )

    async def _row_id(self, memory_id: NotBlankStr) -> int | None:
        """Return the ``rowid`` correlating an entry to its dense vector."""
        async with self._db.execute(sql.SELECT_ROWID, (memory_id,)) as cursor:
            row = await cursor.fetchone()
        return int(row[0]) if row is not None else None

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
            async with self._db.execute(
                sql.SELECT_BY_ID, (memory_id, agent_id)
            ) as cursor:
                row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            self._fail("get", MEMORY_ENTRY_RETRIEVAL_FAILED, exc, memory_id=memory_id)
        return row_to_entry(row) if row is not None else None

    async def delete(
        self,
        agent_id: NotBlankStr,
        memory_id: NotBlankStr,
        /,
    ) -> bool:
        """Delete one entry, its terms and its vector.

        Returns:
            ``True`` when a row was removed.

        Raises:
            QueryError: If the delete fails.
        """
        async with self._write_context():
            try:
                row_id = await self._row_id(memory_id)
                async with self._db.execute(
                    sql.DELETE_BY_ID, (memory_id, agent_id)
                ) as cursor:
                    deleted = cursor.rowcount
                if deleted > 0 and row_id is not None and self._dense_ready:
                    await self._db.execute(
                        sql.delete_vector(self._vector_table), (row_id,)
                    )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._rollback("delete")
                self._fail(
                    "delete", MEMORY_ENTRY_DELETE_FAILED, exc, memory_id=memory_id
                )
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
            async with self._db.execute(
                sql.dense_match(self._vector_table),
                (pack_embedding(spec.embedding), spec.limit),
            ) as cursor:
                hits = await cursor.fetchall()
            if not hits:
                return ()
            distances = {int(r["memory_rowid"]): float(r["distance"]) for r in hits}
            placeholders = ", ".join("?" for _ in distances)
            async with self._db.execute(
                sql.select_by_rowids(where, placeholders),
                (*distances.keys(), *params),
            ) as cursor:
                rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            self._fail("search_dense", MEMORY_ENTRY_RETRIEVAL_FAILED, exc)
        scored = [
            (
                distances[int(row["row_id"])],
                row_to_entry(
                    row,
                    relevance_score=_DISTANCE_TO_SCORE_OFFSET
                    / (_DISTANCE_TO_SCORE_OFFSET + distances[int(row["row_id"])]),
                ),
            )
            for row in rows
        ]
        scored.sort(key=lambda pair: pair[0])
        return tuple(entry for _, entry in scored)

    async def search_lexical(
        self,
        spec: MemoryVectorSearchSpec,
        /,
    ) -> tuple[MemoryEntry, ...]:
        """Rank entries by BM25 over the inverted index.

        Returns:
            Entries in descending BM25 order, empty when no query text
            was supplied or nothing matched.

        Raises:
            QueryError: If the query fails.
        """
        if spec.text is None:
            return ()
        terms = tuple(term_frequencies(spec.text))
        if not terms:
            return ()
        where, params = sql.build_filter_clause(spec)
        placeholders = ", ".join("?" for _ in terms)
        try:
            async with self._db.execute(
                sql.lexical_postings(where, placeholders), (*terms, *params)
            ) as cursor:
                postings = list(await cursor.fetchall())
            if not postings:
                return ()
            async with self._db.execute(sql.corpus_stats(where), tuple(params)) as cur:
                stats = await cur.fetchone()
            async with self._db.execute(
                sql.document_frequency(where, placeholders), (*terms, *params)
            ) as cursor:
                frequency_rows = list(await cursor.fetchall())
        except (sqlite3.Error, aiosqlite.Error) as exc:
            self._fail("search_lexical", MEMORY_ENTRY_RETRIEVAL_FAILED, exc)
        return rank_lexical(postings, stats, frequency_rows, limit=spec.limit)

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
            async with self._db.execute(
                sql.list_filtered(where), (*params, spec.limit)
            ) as cursor:
                rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
            sql.COUNT_BY_AGENT if category is None else (sql.COUNT_BY_AGENT_CATEGORY)
        )
        params = (agent_id,) if category is None else (agent_id, category.value)
        try:
            async with self._db.execute(statement, params) as cursor:
                row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            self._fail("count", MEMORY_ENTRY_COUNT_FAILED, exc, agent_id=agent_id)
        return int(row[0]) if row is not None else 0

    async def purge_expired(self, now: datetime, /) -> int:
        """Delete every entry expiring at or before ``now``.

        Returns:
            The number of rows deleted.

        Raises:
            QueryError: If the delete fails.
        """
        async with self._write_context():
            try:
                async with self._db.execute(
                    sql.SELECT_EXPIRED_IDS, (format_iso_utc(now),)
                ) as cursor:
                    expired = [str(r["memory_id"]) for r in await cursor.fetchall()]
                for memory_id in expired:
                    await self._purge_one(NotBlankStr(memory_id))
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._rollback("purge_expired")
                self._fail("purge_expired", MEMORY_ENTRY_DELETE_FAILED, exc)
        return len(expired)

    async def _purge_one(self, memory_id: NotBlankStr) -> None:
        """Remove one entry and its dense-index row inside an open transaction."""
        row_id = await self._row_id(memory_id)
        await self._db.execute(
            "DELETE FROM memory_entries WHERE memory_id = ?", (memory_id,)
        )
        if row_id is not None and self._dense_ready:
            await self._db.execute(sql.delete_vector(self._vector_table), (row_id,))

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
            async with self._db.execute(
                sql.SELECT_OLDEST_IDS, (agent_id, excess)
            ) as cursor:
                rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            self._fail(
                "oldest_ids", MEMORY_ENTRY_RETRIEVAL_FAILED, exc, agent_id=agent_id
            )
        return tuple(NotBlankStr(str(row["memory_id"])) for row in rows)

    async def _rollback(self, operation: str) -> None:
        """Roll back after a failed write, logging a rollback failure."""
        try:
            await self._db.rollback()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            logger.warning(
                MEMORY_ENTRY_STORE_FAILED,
                phase="rollback",
                operation=operation,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

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


__all__ = ["SQLiteMemoryVectorRepository"]
