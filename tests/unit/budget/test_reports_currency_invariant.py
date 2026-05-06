"""Same-currency invariant tests for report-generation aggregations.

Pins the contract that every site under
:mod:`synthorg.budget.reports` which sums ``r.cost`` over a sequence of
``CostRecord`` rows partitions by currency first.  Mixing currencies
in the input must raise :class:`MixedCurrencyAggregationError` (HTTP
409) **before** any meaningless monetary total is produced.

Covers all four call sites in the module:

* the period-wide ``total_cost`` derivation in
  :meth:`ReportGenerator.generate_report`
* :func:`_build_task_spendings`
* :func:`_build_provider_distribution`
* :func:`_build_model_distribution`
"""

from datetime import UTC, datetime

import pytest

from synthorg.budget.config import BudgetConfig
from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.budget.errors import MixedCurrencyAggregationError
from synthorg.budget.reports import (
    ReportGenerator,
    _build_model_distribution,
    _build_provider_distribution,
    _build_task_spendings,
)
from synthorg.budget.tracker import CostTracker
from tests.unit.budget.conftest import make_cost_record

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 2, 15, 12, 0, 0, tzinfo=UTC)
_START = datetime(2026, 2, 1, tzinfo=UTC)
_END = datetime(2026, 3, 1, tzinfo=UTC)


class TestBuildTaskSpendingsCurrency:
    """``_build_task_spendings`` rejects mixed-currency input per task."""

    def test_uniform_currency_aggregates(self) -> None:
        records = (
            make_cost_record(task_id="t-1", cost=0.10, currency="USD"),
            make_cost_record(task_id="t-1", cost=0.20, currency="USD"),
        )
        result = _build_task_spendings(records)
        assert len(result) == 1
        assert result[0].total_cost == pytest.approx(0.30)
        assert result[0].currency == "USD"

    def test_mixed_currency_per_task_raises(self) -> None:
        """Two rows on the same task in different currencies fail at the
        per-task aggregation, not silently sum."""
        records = (
            make_cost_record(task_id="t-1", cost=0.10, currency="USD"),
            make_cost_record(task_id="t-1", cost=0.20, currency="EUR"),
        )
        with pytest.raises(MixedCurrencyAggregationError) as exc:
            _build_task_spendings(records)
        assert exc.value.currencies == frozenset({"USD", "EUR"})
        assert exc.value.task_id == "t-1"

    def test_uniform_currencies_across_tasks_supported(self) -> None:
        """Different tasks can each have their own currency.

        The invariant is per-task, not global; the gate runs once per
        task bucket.  This is intentional -- the global guard runs in
        :meth:`ReportGenerator.generate_report` before any partitioning.
        Distinct currencies on the two tasks pin the per-task semantics:
        if the builder accidentally added a global guard across the full
        input, this would raise instead of returning two rows.
        """
        records = (
            make_cost_record(task_id="t-1", cost=0.10, currency="USD"),
            make_cost_record(task_id="t-2", cost=0.20, currency="EUR"),
        )
        result = _build_task_spendings(records)
        assert {r.task_id for r in result} == {"t-1", "t-2"}


class TestBuildProviderDistributionCurrency:
    """``_build_provider_distribution`` rejects mixed-currency input per provider."""

    def test_uniform_currency_aggregates(self) -> None:
        records = (
            make_cost_record(provider="prov-a", cost=0.10, currency="USD"),
            make_cost_record(provider="prov-a", cost=0.20, currency="USD"),
        )
        result = _build_provider_distribution(records, total_cost=0.30)
        assert len(result) == 1
        assert result[0].total_cost == pytest.approx(0.30)
        assert result[0].currency == "USD"

    def test_mixed_currency_per_provider_raises(self) -> None:
        records = (
            make_cost_record(provider="prov-a", cost=0.10, currency="USD"),
            make_cost_record(provider="prov-a", cost=0.20, currency="EUR"),
        )
        with pytest.raises(MixedCurrencyAggregationError) as exc:
            _build_provider_distribution(records, total_cost=0.30)
        assert exc.value.currencies == frozenset({"USD", "EUR"})


class TestBuildModelDistributionCurrency:
    """``_build_model_distribution`` rejects mixed-currency input per model."""

    def test_uniform_currency_aggregates(self) -> None:
        records = (
            make_cost_record(
                provider="prov-a",
                model="model-x",
                cost=0.10,
                currency="USD",
            ),
            make_cost_record(
                provider="prov-a",
                model="model-x",
                cost=0.20,
                currency="USD",
            ),
        )
        result = _build_model_distribution(records, total_cost=0.30)
        assert len(result) == 1
        assert result[0].total_cost == pytest.approx(0.30)
        assert result[0].currency == "USD"

    def test_mixed_currency_per_model_raises(self) -> None:
        records = (
            make_cost_record(
                provider="prov-a",
                model="model-x",
                cost=0.10,
                currency="USD",
            ),
            make_cost_record(
                provider="prov-a",
                model="model-x",
                cost=0.20,
                currency="EUR",
            ),
        )
        with pytest.raises(MixedCurrencyAggregationError) as exc:
            _build_model_distribution(records, total_cost=0.30)
        assert exc.value.currencies == frozenset({"USD", "EUR"})


class TestGenerateReportPeriodWideCurrency:
    """``ReportGenerator.generate_report`` aggregates the period total safely.

    Note on coverage: ``CostTracker.record`` already enforces the
    record-vs-config currency match at insertion time, so the mixed-
    currency case cannot reach ``generate_report`` via the public API.
    The defense-in-depth guard inside ``generate_report`` is enforced
    statically by the ``check_currency_aggregation_invariant`` gate; the
    runtime exercises live in the per-partition tests above.
    """

    async def test_uniform_currency_succeeds(self) -> None:
        bc = BudgetConfig(total_monthly=100.0, currency=DEFAULT_CURRENCY)
        tracker = CostTracker(budget_config=bc)
        await tracker.record(
            make_cost_record(cost=0.10, currency=bc.currency, timestamp=_NOW),
        )
        await tracker.record(
            make_cost_record(cost=0.20, currency=bc.currency, timestamp=_NOW),
        )
        gen = ReportGenerator(cost_tracker=tracker, budget_config=bc)
        report = await gen.generate_report(
            start=_START,
            end=_END,
            include_period_comparison=False,
        )
        assert report.summary.period.total_cost == pytest.approx(0.30)
