"""Tests for SQLite circuit breaker state repository."""

import aiosqlite
import pytest

from synthorg.persistence.circuit_breaker_protocol import (
    CircuitBreakerStateRecord,
)
from synthorg.persistence.sqlite.circuit_breaker_repo import (
    SQLiteCircuitBreakerStateRepository,
)
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
