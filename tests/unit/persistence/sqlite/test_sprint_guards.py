"""Tests for the SQLite sprint write guard's unwind behaviour.

The guard's whole job on a non-driver failure is the rollback: SQLite's
transaction is open on the connection every sprint write shares, so a
write that unwinds without one leaves the next writer inheriting it.
"""

import asyncio
import sqlite3
from collections.abc import AsyncIterator

import aiosqlite
import pytest

from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.persistence.sqlite._sprint_guards import write_guard

pytestmark = pytest.mark.unit


@pytest.fixture
async def db() -> AsyncIterator[aiosqlite.Connection]:
    """An in-memory connection carrying one table to write into."""
    conn = await aiosqlite.connect(":memory:")
    try:
        conn.row_factory = aiosqlite.Row
        await conn.execute("CREATE TABLE t (id TEXT PRIMARY KEY)")
        await conn.commit()
        yield conn
    finally:
        await conn.close()


async def _rows(conn: aiosqlite.Connection) -> int:
    """Count the rows currently visible on the connection.

    Returns:
        The row count.
    """
    async with conn.execute("SELECT COUNT(*) FROM t") as cursor:
        row = await cursor.fetchone()
    return int(row[0]) if row is not None else 0


class TestWriteGuardUnwind:
    async def test_cancellation_rolls_the_write_back(
        self, db: aiosqlite.Connection
    ) -> None:
        """Cancellation is the likeliest way to unwind, and is not an Exception.

        Shutdown drains these writes, so this is the case the rollback
        exists for. Caught as ``Exception`` the transaction stayed open and
        the uncommitted row was still visible on the shared connection.
        """

        async def _write_then_cancel() -> None:
            async with write_guard(
                db, operation="save", doing="saving", sprint_id="s-1"
            ):
                await db.execute("INSERT INTO t (id) VALUES ('a')")
                assert await _rows(db) == 1
                raise asyncio.CancelledError

        with pytest.raises(asyncio.CancelledError):
            await _write_then_cancel()

        assert await _rows(db) == 0

    async def test_an_ordinary_error_rolls_the_write_back(
        self, db: aiosqlite.Connection
    ) -> None:
        """The arm that already worked, pinned so widening did not lose it."""

        async def _write_then_refuse() -> None:
            async with write_guard(
                db, operation="save", doing="saving", sprint_id="s-1"
            ):
                await db.execute("INSERT INTO t (id) VALUES ('a')")
                msg = "derived row is unreadable"
                raise ValueError(msg)

        with pytest.raises(ValueError, match="unreadable"):
            await _write_then_refuse()

        assert await _rows(db) == 0

    async def test_an_integrity_error_carries_its_sqlstate(
        self, db: aiosqlite.Connection
    ) -> None:
        """The refusal is classified rather than stringified.

        Built by hand the constraint field carried the driver's whole
        message and the SQLSTATE was absent, so a caller could not tell a
        unique violation from any other integrity failure.
        """
        await db.execute("INSERT INTO t (id) VALUES ('a')")
        await db.commit()

        with pytest.raises(ConstraintViolationError) as excinfo:
            async with write_guard(
                db, operation="save", doing="saving", sprint_id="s-1"
            ):
                await db.execute("INSERT INTO t (id) VALUES ('a')")

        assert excinfo.value.sqlstate is not None

    async def test_a_driver_error_becomes_a_query_error(
        self, db: aiosqlite.Connection
    ) -> None:
        """Anything else the driver raises is the vaguer domain error."""
        with pytest.raises(QueryError):
            async with write_guard(
                db, operation="save", doing="saving", sprint_id="s-1"
            ):
                raise sqlite3.OperationalError
