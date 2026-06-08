"""Tests for SQLite circuit breaker state repository."""

import sqlite3
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from synthorg.core.persistence_errors import QueryError
from synthorg.persistence.circuit_breaker_protocol import (
    CircuitBreakerStateRecord,
)
from synthorg.persistence.sqlite.circuit_breaker_repo import (
    SQLiteCircuitBreakerStateRepository,
)
from tests._shared import mock_of
from tests._shared.persistence import make_private_write_context


@pytest.mark.unit
class TestSQLiteCircuitBreakerStateRepository:
    @pytest.fixture
    def repo(
        self, migrated_db: aiosqlite.Connection
    ) -> SQLiteCircuitBreakerStateRepository:
        return SQLiteCircuitBreakerStateRepository(
            migrated_db, write_context=make_private_write_context()
        )

    async def test_save_and_load_all(
        self,
        repo: SQLiteCircuitBreakerStateRepository,
    ) -> None:
        r1 = CircuitBreakerStateRecord(
            pair_key_a="a",
            pair_key_b="b",
            bounce_count=1,
            trip_count=2,
            opened_at=100.0,
        )
        r2 = CircuitBreakerStateRecord(
            pair_key_a="c",
            pair_key_b="d",
            bounce_count=0,
            trip_count=1,
        )
        await repo.save(r1)
        await repo.save(r2)

        results = await repo.load_all()
        assert len(results) == 2
        by_key = {(r.pair_key_a, r.pair_key_b): r for r in results}
        assert by_key[("a", "b")].trip_count == 2
        assert by_key[("a", "b")].opened_at == 100.0
        assert by_key[("c", "d")].trip_count == 1
        assert by_key[("c", "d")].opened_at is None

    async def test_upsert_overwrites(
        self,
        repo: SQLiteCircuitBreakerStateRepository,
    ) -> None:
        r1 = CircuitBreakerStateRecord(
            pair_key_a="a",
            pair_key_b="b",
            bounce_count=1,
            trip_count=1,
        )
        await repo.save(r1)

        r2 = CircuitBreakerStateRecord(
            pair_key_a="a",
            pair_key_b="b",
            bounce_count=0,
            trip_count=3,
            opened_at=200.0,
        )
        await repo.save(r2)

        results = await repo.load_all()
        assert len(results) == 1
        assert results[0].trip_count == 3
        assert results[0].opened_at == 200.0

    async def test_delete_removes_entry(
        self,
        repo: SQLiteCircuitBreakerStateRepository,
    ) -> None:
        r1 = CircuitBreakerStateRecord(
            pair_key_a="a",
            pair_key_b="b",
            bounce_count=0,
            trip_count=1,
        )
        await repo.save(r1)

        deleted = await repo.delete(("a", "b"))
        assert deleted is True

        results = await repo.load_all()
        assert len(results) == 0

    async def test_delete_nonexistent_returns_false(
        self,
        repo: SQLiteCircuitBreakerStateRepository,
    ) -> None:
        deleted = await repo.delete(("x", "y"))
        assert deleted is False

    async def test_load_all_empty(
        self,
        repo: SQLiteCircuitBreakerStateRepository,
    ) -> None:
        results = await repo.load_all()
        assert results == ()


@pytest.mark.unit
class TestRollbackGuardNarrowing:
    """Pin the narrowed ``_rollback_quietly`` driver-error semantics.

    The guard is identical across the six SQLite repos that carry it
    (ceremony / circuit-breaker / meeting-cooldown / risk-override /
    ssrf-violation / tracked-container); this exercises the shared shape.
    """

    @staticmethod
    def _record() -> CircuitBreakerStateRecord:
        return CircuitBreakerStateRecord(
            pair_key_a="a", pair_key_b="b", bounce_count=0, trip_count=1
        )

    async def test_driver_error_on_rollback_is_swallowed(self) -> None:
        """A driver error during rollback is swallowed; ``QueryError`` wins.

        The primary failure (the ``execute`` driver error wrapped as
        ``QueryError``) must remain the operative exception even when the
        best-effort rollback itself raises a driver error.
        """
        db = mock_of[aiosqlite.Connection](
            execute=AsyncMock(side_effect=sqlite3.OperationalError("write failed")),
            rollback=AsyncMock(side_effect=sqlite3.OperationalError("rollback failed")),
            commit=AsyncMock(),
        )
        repo = SQLiteCircuitBreakerStateRepository(
            db, write_context=make_private_write_context()
        )
        with pytest.raises(QueryError):
            await repo.save(self._record())
        db.rollback.assert_awaited_once()

    async def test_non_driver_error_on_rollback_propagates(self) -> None:
        """A non-driver rollback error now propagates (no longer swallowed).

        Before narrowing, the broad ``except Exception`` swallowed any
        rollback failure. Narrowed to the driver surface, a non-driver
        error (a real bug) propagates instead of being hidden.
        """
        db = mock_of[aiosqlite.Connection](
            execute=AsyncMock(side_effect=sqlite3.OperationalError("write failed")),
            rollback=AsyncMock(side_effect=RuntimeError("unexpected")),
            commit=AsyncMock(),
        )
        repo = SQLiteCircuitBreakerStateRepository(
            db, write_context=make_private_write_context()
        )
        with pytest.raises(RuntimeError, match="unexpected"):
            await repo.save(self._record())

    async def test_connection_closed_during_rollback_is_swallowed(self) -> None:
        """aiosqlite's ``ValueError("Connection closed")`` is swallowed.

        A rollback on a closed connection raises a bare ``ValueError`` (not
        a ``sqlite3.Error``); the guard treats it as a driver-level failure
        so the primary ``QueryError`` stays operative rather than being
        masked by the connection-state error.
        """
        db = mock_of[aiosqlite.Connection](
            execute=AsyncMock(side_effect=sqlite3.OperationalError("write failed")),
            rollback=AsyncMock(side_effect=ValueError("Connection closed")),
            commit=AsyncMock(),
        )
        repo = SQLiteCircuitBreakerStateRepository(
            db, write_context=make_private_write_context()
        )
        with pytest.raises(QueryError):
            await repo.save(self._record())
        db.rollback.assert_awaited_once()
