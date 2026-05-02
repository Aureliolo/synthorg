"""Currency-invariant tests for budget trends.

Both ``bucket_cost_records`` and ``project_daily_spend`` aggregate
``record.cost`` across cost records.  Mixing currencies in the input
silently produces a meaningless monetary aggregate, so both call
``_assert_single_currency`` at the boundary and raise
``MixedCurrencyAggregationError`` (HTTP 409) instead.
"""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.budget.cost_record import CostRecord
from synthorg.budget.errors import MixedCurrencyAggregationError
from synthorg.budget.trends import (
    BucketSize,
    bucket_cost_records,
    project_daily_spend,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)


def _record(
    currency: str,
    cost: float = 0.10,
    ts: datetime | None = None,
) -> CostRecord:
    return CostRecord(
        agent_id="agent-a",
        task_id="task-001",
        provider="test-provider",
        model="test-small-001",
        input_tokens=100,
        output_tokens=50,
        cost=cost,
        currency=currency,
        timestamp=ts or _NOW,
    )


class TestBucketCostRecordsCurrency:
    """`bucket_cost_records` rejects mixed-currency input."""

    def test_single_currency_aggregates_cleanly(self) -> None:
        records = (
            _record("EUR", 0.10),
            _record("EUR", 0.20),
        )
        result = bucket_cost_records(
            records,
            _NOW,
            _NOW + timedelta(hours=1),
            BucketSize.HOUR,
        )
        assert result[0].value == pytest.approx(0.30)

    def test_mixed_currency_raises(self) -> None:
        records = (
            _record("EUR", 0.10),
            _record("USD", 0.20),
        )
        with pytest.raises(MixedCurrencyAggregationError) as exc:
            bucket_cost_records(
                records,
                _NOW,
                _NOW + timedelta(hours=1),
                BucketSize.HOUR,
            )
        assert exc.value.currencies == frozenset({"EUR", "USD"})

    def test_empty_records_no_error(self) -> None:
        result = bucket_cost_records(
            (),
            _NOW,
            _NOW + timedelta(hours=1),
            BucketSize.HOUR,
        )
        assert all(point.value == 0.0 for point in result)


class TestProjectDailySpendCurrency:
    """`project_daily_spend` rejects mixed-currency input."""

    def test_single_currency_projects_cleanly(self) -> None:
        records = (
            _record("EUR", 1.00, _NOW - timedelta(days=2)),
            _record("EUR", 2.00, _NOW - timedelta(days=1)),
        )
        forecast = project_daily_spend(records, horizon_days=7, now=_NOW)
        assert forecast.avg_daily_spend > 0

    def test_mixed_currency_raises(self) -> None:
        records = (
            _record("EUR", 1.00, _NOW - timedelta(days=2)),
            _record("USD", 2.00, _NOW - timedelta(days=1)),
        )
        with pytest.raises(MixedCurrencyAggregationError) as exc:
            project_daily_spend(records, horizon_days=7, now=_NOW)
        assert exc.value.currencies == frozenset({"EUR", "USD"})

    def test_empty_records_no_error(self) -> None:
        forecast = project_daily_spend((), horizon_days=7, now=_NOW)
        assert forecast.avg_daily_spend == 0.0
        assert forecast.confidence == 0.0
