"""Currency-invariant tests for budget trends.

Both ``bucket_cost_records`` and ``project_daily_spend`` aggregate
``record.cost`` across cost records.  Mixing currencies in the input
silently produces a meaningless monetary aggregate, so both call
``assert_currencies_match`` at the boundary and raise
``MixedCurrencyAggregationError`` (HTTP 409) instead.
"""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.budget.cost_record import CostRecord
from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.budget.errors import MixedCurrencyAggregationError
from synthorg.budget.trends import (
    BucketSize,
    bucket_cost_records,
    project_daily_spend,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 5, 1, 12, 0, 0, tzinfo=UTC)
# ``CurrencyCode`` validates against the project's known ISO 4217
# allowlist, so the alternate currency MUST be a real ISO code -- we
# can't substitute a synthetic string here. ``EUR`` is picked only
# because it is distinct from ``DEFAULT_CURRENCY``; the specific
# choice is irrelevant to what these tests prove.
_PRIMARY_CURRENCY = DEFAULT_CURRENCY
_ALTERNATE_CURRENCY = "USD" if DEFAULT_CURRENCY != "USD" else "EUR"


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
            _record(_PRIMARY_CURRENCY, 0.10),
            _record(_PRIMARY_CURRENCY, 0.20),
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
            _record(_PRIMARY_CURRENCY, 0.10),
            _record(_ALTERNATE_CURRENCY, 0.20),
        )
        with pytest.raises(MixedCurrencyAggregationError) as exc:
            bucket_cost_records(
                records,
                _NOW,
                _NOW + timedelta(hours=1),
                BucketSize.HOUR,
            )
        assert exc.value.currencies == frozenset(
            {_PRIMARY_CURRENCY, _ALTERNATE_CURRENCY},
        )

    def test_empty_records_no_error(self) -> None:
        result = bucket_cost_records(
            (),
            _NOW,
            _NOW + timedelta(hours=1),
            BucketSize.HOUR,
        )
        assert all(point.value == 0.0 for point in result)

    def test_out_of_window_mixed_currency_no_error(self) -> None:
        """Mixed currencies entirely outside ``[start, end)`` must not raise.

        The ``assert_currencies_match`` guard runs on the post-filter
        slice, so out-of-range rows do not contribute to the
        aggregation and do not need to be currency-uniform. Without
        this regression, a partial-range query against a long-lived
        multi-currency dataset would raise even though the bucket
        itself is consistent.
        """
        records = (
            _record(
                _PRIMARY_CURRENCY,
                0.10,
                ts=_NOW - timedelta(hours=10),
            ),
            _record(
                _ALTERNATE_CURRENCY,
                0.20,
                ts=_NOW - timedelta(hours=20),
            ),
        )
        result = bucket_cost_records(
            records,
            _NOW,
            _NOW + timedelta(hours=1),
            BucketSize.HOUR,
        )
        assert all(point.value == 0.0 for point in result)


class TestProjectDailySpendCurrency:
    """`project_daily_spend` rejects mixed-currency input."""

    def test_single_currency_projects_cleanly(self) -> None:
        records = (
            _record(_PRIMARY_CURRENCY, 1.00, _NOW - timedelta(days=2)),
            _record(_PRIMARY_CURRENCY, 2.00, _NOW - timedelta(days=1)),
        )
        forecast = project_daily_spend(records, horizon_days=7, now=_NOW)
        assert forecast.avg_daily_spend > 0

    def test_mixed_currency_raises(self) -> None:
        records = (
            _record(_PRIMARY_CURRENCY, 1.00, _NOW - timedelta(days=2)),
            _record(_ALTERNATE_CURRENCY, 2.00, _NOW - timedelta(days=1)),
        )
        with pytest.raises(MixedCurrencyAggregationError) as exc:
            project_daily_spend(records, horizon_days=7, now=_NOW)
        assert exc.value.currencies == frozenset(
            {_PRIMARY_CURRENCY, _ALTERNATE_CURRENCY},
        )

    def test_empty_records_no_error(self) -> None:
        forecast = project_daily_spend((), horizon_days=7, now=_NOW)
        assert forecast.avg_daily_spend == 0.0
        assert forecast.confidence == 0.0


class TestComputeDailySpendDirectGuard:
    """`_compute_daily_spend` enforces the same-currency invariant directly.

    `project_daily_spend` already exercises the full code path, but the
    private helper is also reachable from future call sites; this class
    pins the guard at the helper boundary.
    """

    def test_mixed_currency_raises(self) -> None:
        from synthorg.budget.trends import _compute_daily_spend

        records = (
            _record(_PRIMARY_CURRENCY, 1.00, _NOW - timedelta(days=1)),
            _record(_ALTERNATE_CURRENCY, 2.00, _NOW),
        )
        with pytest.raises(MixedCurrencyAggregationError):
            _compute_daily_spend(records)
