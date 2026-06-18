"""Unit tests for SQLiteProjectCostAggregateRepository."""

import sqlite3
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

from synthorg.core.persistence_errors import QueryError
from synthorg.persistence.sqlite.project_cost_aggregate_repo import (
    SQLiteProjectCostAggregateRepository,
)
from tests._shared.persistence import make_private_write_context

if TYPE_CHECKING:
    import aiosqlite


@pytest.mark.unit
class TestSQLiteProjectCostAggregateRepository:
    """Tests for the durable project cost aggregate repo."""

    async def test_get_returns_none_when_not_found(
        self,
        migrated_db: aiosqlite.Connection,
    ) -> None:
        repo = SQLiteProjectCostAggregateRepository(
            migrated_db, write_context=make_private_write_context()
        )
        result = await repo.get("proj-nonexistent")
        assert result is None

    async def test_increment_creates_new_aggregate(
        self,
        migrated_db: aiosqlite.Connection,
    ) -> None:
        repo = SQLiteProjectCostAggregateRepository(
            migrated_db, write_context=make_private_write_context()
        )
        agg = await repo.increment("proj-1", 1.5, 100, 50, currency="USD")

        assert agg.project_id == "proj-1"
        assert agg.total_cost == 1.5
        assert agg.currency == "USD"
        assert agg.total_input_tokens == 100
        assert agg.total_output_tokens == 50
        assert agg.record_count == 1

    async def test_increment_updates_existing(
        self,
        migrated_db: aiosqlite.Connection,
    ) -> None:
        repo = SQLiteProjectCostAggregateRepository(
            migrated_db, write_context=make_private_write_context()
        )
        await repo.increment("proj-1", 1.0, 100, 50, currency="USD")
        agg = await repo.increment("proj-1", 2.0, 200, 100, currency="USD")

        assert agg.total_cost == pytest.approx(3.0)
        assert agg.currency == "USD"
        assert agg.total_input_tokens == 300
        assert agg.total_output_tokens == 150
        assert agg.record_count == 2

    async def test_multiple_increments_accumulate(
        self,
        migrated_db: aiosqlite.Connection,
    ) -> None:
        repo = SQLiteProjectCostAggregateRepository(
            migrated_db, write_context=make_private_write_context()
        )
        for _ in range(5):
            await repo.increment("proj-1", 0.1, 10, 5, currency="USD")

        agg = await repo.get("proj-1")
        assert agg is not None
        assert agg.total_cost == pytest.approx(0.5)
        assert agg.total_input_tokens == 50
        assert agg.total_output_tokens == 25
        assert agg.record_count == 5

    async def test_get_after_increment(
        self,
        migrated_db: aiosqlite.Connection,
    ) -> None:
        repo = SQLiteProjectCostAggregateRepository(
            migrated_db, write_context=make_private_write_context()
        )
        await repo.increment("proj-1", 3.0, 500, 200, currency="USD")

        agg = await repo.get("proj-1")
        assert agg is not None
        assert agg.total_cost == 3.0
        assert agg.currency == "USD"
        assert agg.total_input_tokens == 500
        assert agg.total_output_tokens == 200
        assert agg.record_count == 1

    async def test_isolation_between_projects(
        self,
        migrated_db: aiosqlite.Connection,
    ) -> None:
        repo = SQLiteProjectCostAggregateRepository(
            migrated_db, write_context=make_private_write_context()
        )
        await repo.increment("proj-a", 10.0, 1000, 500, currency="USD")
        await repo.increment("proj-b", 5.0, 200, 100, currency="USD")

        agg_a = await repo.get("proj-a")
        agg_b = await repo.get("proj-b")

        assert agg_a is not None
        assert agg_b is not None
        assert agg_a.total_cost == 10.0
        assert agg_b.total_cost == 5.0

    async def test_last_updated_changes(
        self,
        migrated_db: aiosqlite.Connection,
    ) -> None:
        repo = SQLiteProjectCostAggregateRepository(
            migrated_db, write_context=make_private_write_context()
        )
        agg1 = await repo.increment("proj-1", 1.0, 10, 5, currency="USD")
        agg2 = await repo.increment("proj-1", 1.0, 10, 5, currency="USD")

        assert agg2.last_updated >= agg1.last_updated

    async def test_zero_cost_increment(
        self,
        migrated_db: aiosqlite.Connection,
    ) -> None:
        repo = SQLiteProjectCostAggregateRepository(
            migrated_db, write_context=make_private_write_context()
        )
        agg = await repo.increment("proj-1", 0.0, 0, 0, currency="USD")

        assert agg.total_cost == 0.0
        assert agg.record_count == 1

        agg2 = await repo.increment("proj-1", 0.0, 0, 0, currency="USD")
        assert agg2.record_count == 2

    async def test_increment_rejects_currency_mismatch(
        self,
        migrated_db: aiosqlite.Connection,
    ) -> None:
        from synthorg.budget.errors import (
            MixedCurrencyAggregationError,
        )

        repo = SQLiteProjectCostAggregateRepository(
            migrated_db, write_context=make_private_write_context()
        )
        await repo.increment("proj-1", 1.0, 10, 5, currency="USD")
        with pytest.raises(MixedCurrencyAggregationError) as exc_info:
            await repo.increment("proj-1", 1.0, 10, 5, currency="EUR")
        assert exc_info.value.currencies == frozenset({"USD", "EUR"})
        assert exc_info.value.project_id == "proj-1"

        # Fail-closed: the rejected EUR increment must NOT have
        # mutated the durable aggregate.  After the exception, the
        # row must still match the first-write state -- otherwise a
        # retry (or a currency reconfiguration) would double-count
        # the offending entry.
        after = await repo.get("proj-1")
        assert after is not None
        assert after.currency == "USD"
        assert after.total_cost == pytest.approx(1.0)
        assert after.total_input_tokens == 10
        assert after.total_output_tokens == 5
        assert after.record_count == 1

    async def test_get_raises_query_error_on_db_failure(
        self,
        migrated_db: aiosqlite.Connection,
    ) -> None:
        repo = SQLiteProjectCostAggregateRepository(
            migrated_db, write_context=make_private_write_context()
        )
        with (
            patch.object(
                migrated_db,
                "execute",
                new_callable=MagicMock,
                side_effect=sqlite3.OperationalError("disk I/O error"),
            ),
            pytest.raises(QueryError),
        ):
            await repo.get("proj-1")

    async def test_increment_raises_query_error_on_db_failure(
        self,
        migrated_db: aiosqlite.Connection,
    ) -> None:
        repo = SQLiteProjectCostAggregateRepository(
            migrated_db, write_context=make_private_write_context()
        )
        with (
            patch.object(
                migrated_db,
                "execute",
                new_callable=MagicMock,
                side_effect=sqlite3.OperationalError("disk I/O error"),
            ),
            pytest.raises(QueryError),
        ):
            await repo.increment("proj-1", 1.0, 100, 50, currency="USD")

    @pytest.mark.parametrize(
        ("cost", "input_tokens", "output_tokens"),
        [
            (-1.0, 100, 50),
            (1.0, -1, 50),
            (1.0, 100, -1),
            (float("nan"), 100, 50),
            (float("inf"), 100, 50),
        ],
        ids=[
            "negative_cost",
            "negative_input_tokens",
            "negative_output_tokens",
            "nan_cost",
            "inf_cost",
        ],
    )
    async def test_increment_rejects_invalid_deltas(
        self,
        migrated_db: aiosqlite.Connection,
        cost: float,
        input_tokens: int,
        output_tokens: int,
    ) -> None:
        repo = SQLiteProjectCostAggregateRepository(
            migrated_db, write_context=make_private_write_context()
        )
        with pytest.raises(ValueError, match="non-negative"):
            await repo.increment(
                "proj-1", cost, input_tokens, output_tokens, currency="USD"
            )

    async def test_increment_if_unseen_dedups_atomically(
        self,
        migrated_db: aiosqlite.Connection,
    ) -> None:
        from datetime import UTC, datetime

        repo = SQLiteProjectCostAggregateRepository(
            migrated_db, write_context=make_private_write_context()
        )
        now = datetime.now(UTC)
        first, first_new = await repo.increment_if_unseen(
            "proj-1",
            1.0,
            10,
            5,
            currency="USD",
            claim_id="claim-x",
            now=now,
            ttl_seconds=3600.0,
        )
        assert first_new is True
        assert first is not None
        assert first.record_count == 1

        # Redelivery of the same claim is a no-op: no second bill.
        second, second_new = await repo.increment_if_unseen(
            "proj-1",
            1.0,
            10,
            5,
            currency="USD",
            claim_id="claim-x",
            now=now,
            ttl_seconds=3600.0,
        )
        assert second_new is False
        assert second is None

        after = await repo.get("proj-1")
        assert after is not None
        assert after.total_cost == pytest.approx(1.0)
        assert after.record_count == 1

    async def test_increment_if_unseen_distinct_claims_accumulate(
        self,
        migrated_db: aiosqlite.Connection,
    ) -> None:
        from datetime import UTC, datetime

        repo = SQLiteProjectCostAggregateRepository(
            migrated_db, write_context=make_private_write_context()
        )
        now = datetime.now(UTC)
        for claim in ("a", "b", "c"):
            _, was_new = await repo.increment_if_unseen(
                "proj-1",
                0.5,
                10,
                5,
                currency="USD",
                claim_id=claim,
                now=now,
                ttl_seconds=3600.0,
            )
            assert was_new is True

        after = await repo.get("proj-1")
        assert after is not None
        assert after.total_cost == pytest.approx(1.5)
        assert after.record_count == 3
