"""Same-currency invariant tests for the shared aggregation primitives.

Direct coverage for the helper functions in
:mod:`synthorg.budget._aggregation` and
:mod:`synthorg.budget._optimizer_helpers` that aggregate ``cost`` over
``CostRecord`` sequences.  These primitives are the lowest layer of the
budget aggregation stack; if the gate is ever bypassed at a higher
layer, the same-currency invariant must still hold here.
"""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.budget._aggregation import sum_cost
from synthorg.budget._optimizer_helpers import _compute_window_costs
from synthorg.budget.errors import MixedCurrencyAggregationError
from tests.unit.budget.conftest import make_cost_record

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)


class TestSumCostCurrency:
    """``sum_cost`` enforces the same-currency invariant before reducing."""

    def test_uniform_currency_sums(self) -> None:
        records = (
            make_cost_record(cost=0.10, currency="USD"),
            make_cost_record(cost=0.20, currency="USD"),
        )
        assert sum_cost(records) == pytest.approx(0.30)

    def test_mixed_currency_raises(self) -> None:
        records = (
            make_cost_record(cost=0.10, currency="USD"),
            make_cost_record(cost=0.20, currency="EUR"),
        )
        with pytest.raises(MixedCurrencyAggregationError) as exc:
            sum_cost(records)
        assert exc.value.currencies == frozenset({"USD", "EUR"})

    def test_empty_returns_zero(self) -> None:
        assert sum_cost(()) == 0.0


class TestComputeWindowCostsCurrency:
    """``_compute_window_costs`` rejects mixed-currency input."""

    def test_uniform_currency_buckets(self) -> None:
        ws_a = _NOW
        ws_b = _NOW + timedelta(days=1)
        records = (
            make_cost_record(cost=0.10, currency="USD", timestamp=ws_a),
            make_cost_record(cost=0.20, currency="USD", timestamp=ws_b),
        )
        result = _compute_window_costs(
            records,
            (ws_a, ws_b),
            timedelta(days=1),
        )
        assert len(result) == 2
        assert result[0] == pytest.approx(0.10)
        assert result[1] == pytest.approx(0.20)

    def test_mixed_currency_raises(self) -> None:
        ws = _NOW
        records = (
            make_cost_record(cost=0.10, currency="USD", timestamp=ws),
            make_cost_record(cost=0.20, currency="EUR", timestamp=ws),
        )
        with pytest.raises(MixedCurrencyAggregationError) as exc:
            _compute_window_costs(
                records,
                (ws,),
                timedelta(days=1),
            )
        assert exc.value.currencies == frozenset({"USD", "EUR"})
