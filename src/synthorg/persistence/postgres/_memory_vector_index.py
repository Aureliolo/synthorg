"""Dense-column and HNSW-index lifecycle for the Postgres memory repository.

Building the dense column is a cohesive slice with its own hazards, none of
which the repository's read/write paths share: ``CONCURRENTLY`` cannot run in
a transaction, concurrent builders need an advisory lock, a crashed build
leaves an index that looks present, and the pgvector extension needs a
privilege a least-privileged role does not have. It lives in its own mixin so
the repository module stays about storing and recalling entries. The mixin
reaches back for ``_pool`` and ``_dimensions``; the ``TYPE_CHECKING`` block
declares that surface so ``mypy`` checks the mixin in isolation.
"""

import contextlib
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, LiteralString, cast

import psycopg
from psycopg import AsyncConnection
from psycopg.rows import TupleRow

import synthorg.persistence.postgres._memory_vector_sql as sql
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import (
    MEMORY_DENSE_COLUMN_STALE,
    MEMORY_DENSE_INDEX_BUILD_CONTENDED,
    MEMORY_DENSE_INDEX_INVALID,
    MEMORY_DENSE_INDEX_PERMISSION_DENIED,
    MEMORY_DENSE_INDEX_SCAN_FAILED,
    MEMORY_DENSE_INDEX_WIDTH_CHANGED,
    MEMORY_DENSE_SESSION_DISCARDED,
)

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = get_logger(__name__)

#: Row shape the pool hands out, matching the host repository's own alias.
type _PoolConnection = AsyncConnection[TupleRow]


class DenseIndexLifecycleMixin:
    """Dense-column build + diagnostics mixed into the repository."""

    if TYPE_CHECKING:
        _pool: AsyncConnectionPool[AsyncConnection[TupleRow]]
        _dimensions: int | None

    async def _build_dense_column(
        self,
        conn: _PoolConnection,
        spec: sql.DenseColumnSpec,
    ) -> None:
        """Create the dense column and index under the build lock.

        Raises:
            psycopg.Error: If any DDL statement fails.
        """
        # CREATE INDEX CONCURRENTLY cannot run inside a transaction block,
        # and a plain CREATE INDEX would hold a lock that blocks memory
        # writes for the whole build on an established corpus. Autocommit
        # first, before any statement opens a transaction.
        original_autocommit = bool(getattr(conn, "autocommit", False))
        await conn.set_autocommit(True)
        try:
            # Serialise builders across processes/pods sharing this
            # database: CONCURRENTLY + IF NOT EXISTS is not race-free
            # between two simultaneous builds, and a crash mid-build can
            # leave an INVALID index. A session advisory lock keyed on the
            # column lets at most one process build a given width at a time.
            await self._acquire_build_lock(conn, spec)
            try:
                await self._ensure_extension(conn)
                await conn.execute(sql.add_vector_column(spec))
                if spec.indexable:
                    await self._drop_if_invalid(conn, spec)
                    await conn.execute(sql.create_vector_index(spec))
            finally:
                await self._restore_or_discard(
                    conn,
                    "release_build_lock",
                    lambda: conn.execute(sql.RELEASE_INDEX_BUILD_LOCK, (spec.name,)),
                )
        finally:
            await self._restore_or_discard(
                conn,
                "restore_autocommit",
                lambda: conn.set_autocommit(original_autocommit),
            )

    async def _acquire_build_lock(
        self,
        conn: _PoolConnection,
        spec: sql.DenseColumnSpec,
    ) -> None:
        """Take the build lock, bounded so a sibling build cannot hang boot.

        Raises:
            psycopg.errors.QueryCanceled: If the lock was still held at the
                deadline, after reporting the contention as its own
                condition.
            psycopg.Error: If the lock statement fails for any other reason.
        """
        cursor = await conn.execute(sql.SHOW_STATEMENT_TIMEOUT)
        row = await cursor.fetchone()
        # ``0`` is how Postgres spells "no timeout", so it is also the safe
        # assumption when the probe answers nothing.
        previous = str(row[0]) if row is not None else "0"
        await conn.execute(sql.SET_STATEMENT_TIMEOUT, (sql.INDEX_BUILD_LOCK_WAIT,))
        try:
            await conn.execute(sql.ACQUIRE_INDEX_BUILD_LOCK, (spec.name,))
        except psycopg.errors.QueryCanceled:
            logger.warning(
                MEMORY_DENSE_INDEX_BUILD_CONTENDED,
                dimensions=self._dimensions,
                index=sql.index_name(spec),
                note=(
                    "another process is building this width; recall stays "
                    "lexical until it finishes and readiness is retried"
                ),
            )
            raise
        finally:
            # The build itself is legitimately long-running, so the deadline
            # must not outlive the wait it was set for -- including on the
            # timeout path, where this connection goes back to the pool and
            # serves queries that never asked to be bounded.
            await self._restore_or_discard(
                conn,
                "restore_statement_timeout",
                lambda: conn.execute(sql.SET_STATEMENT_TIMEOUT, (previous,)),
            )

    async def _restore_or_discard(
        self,
        conn: _PoolConnection,
        action: str,
        restore: Callable[[], Awaitable[object]],
    ) -> None:
        """Undo one session change, closing the connection if that fails.

        These run in ``finally`` blocks, so they must not replace the error
        that explains the failure. Suppressing outright is not neutral
        either: the connection is pooled, so a lock left held would block
        every later builder on this database, and a leftover
        ``statement_timeout`` would silently bound queries that never asked
        for one. Closing is what makes the pool discard the session rather
        than hand the damage to the next caller.
        """
        try:
            await restore()
        except psycopg.Error as exc:
            logger.error(
                MEMORY_DENSE_SESSION_DISCARDED,
                action=action,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            with contextlib.suppress(Exception):
                await conn.close()

    async def _drop_if_invalid(
        self,
        conn: _PoolConnection,
        spec: sql.DenseColumnSpec,
    ) -> None:
        """Drop an index a previous build left ``INVALID``.

        ``CREATE INDEX CONCURRENTLY IF NOT EXISTS`` matches on the name
        alone, so an index a crashed build left behind is treated as
        already present and never rebuilt. Nothing raises, the readiness
        flag latches, and every dense query silently falls back to a
        sequential scan for the life of the deployment.

        Raises:
            psycopg.Error: If the validity probe or the drop fails.
        """
        cursor = await conn.execute(sql.SELECT_INDEX_IS_VALID, (sql.index_name(spec),))
        row = await cursor.fetchone()
        if row is None or bool(row[0]):
            return
        logger.warning(
            MEMORY_DENSE_INDEX_INVALID,
            dimensions=self._dimensions,
            index=sql.index_name(spec),
        )
        await conn.execute(sql.drop_vector_index(spec))

    async def _ensure_extension(self, conn: _PoolConnection) -> None:
        """Install pgvector, distinguishing "absent" from "not permitted".

        ``CREATE EXTENSION`` needs superuser because pgvector is not a
        trusted extension. A least-privilege production role therefore
        fails here while CI and the bundled image succeed, which is the
        one degradation an operator is least likely to expect, so it is
        reported as its own condition rather than folded into a generic
        "index unavailable" warning.

        Raises:
            InsufficientPrivilege: If the role may not create the
                extension, after reporting it as its own condition.
            psycopg.Error: If the statement fails for any other reason.
        """
        try:
            await conn.execute("CREATE EXTENSION IF NOT EXISTS vector")
        except psycopg.errors.InsufficientPrivilege:
            logger.error(
                MEMORY_DENSE_INDEX_PERMISSION_DENIED,
                dimensions=self._dimensions,
                note=(
                    "the database role may not CREATE EXTENSION vector; install "
                    "pgvector as a superuser during provisioning, otherwise "
                    "recall stays lexical-only"
                ),
            )
            raise

    async def _report_orphaned_widths(self, current_column: LiteralString) -> None:
        """Log every dense column left over from a different width.

        Best-effort: this is a diagnostic, so a failure to look must not
        cost the caller a working dense index.

        Args:
            current_column: The column in use, excluded from the report.
        """
        try:
            async with self._pool.connection() as conn:
                cursor = await conn.execute(
                    sql.SELECT_VECTOR_COLUMNS, (current_column,)
                )
                stale = [str(row[0]) for row in await cursor.fetchall()]
                for column in stale:
                    await self._report_one_orphan(conn, column)
        except psycopg.Error as exc:
            logger.warning(
                MEMORY_DENSE_INDEX_SCAN_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def _report_one_orphan(self, conn: _PoolConnection, column: str) -> None:
        """Report one leftover dense column by whether it still holds rows.

        Raises:
            psycopg.Error: If the row count cannot be read.
        """
        # mypy erases LiteralString to str and calls this redundant;
        # pyright needs it for psycopg's query types.
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
            return
        # An empty leftover strands no vectors, so it is not an error, but it
        # is still schema drift nothing else reports: a width whose element
        # type later changed leaves a column under the old name that no query
        # will ever touch again.
        logger.info(
            MEMORY_DENSE_COLUMN_STALE,
            dimensions=self._dimensions,
            previous_index=column,
        )


__all__ = ["DenseIndexLifecycleMixin"]
