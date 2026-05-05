"""Same-currency invariant tests for cost aggregation.

Covers the central ``_aggregate`` helper + every public CostTracker
aggregator + the department rollup.  Hypothesis properties check that
any stream mixing >= 2 currencies always raises, regardless of order,
size, or currency combination.
"""

from datetime import UTC, datetime

import pytest
from hypothesis import example, given
from hypothesis import strategies as st

from synthorg.budget._tracker_helpers import _aggregate
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.currency import assert_currencies_match
from synthorg.budget.errors import MixedCurrencyAggregationError
from synthorg.budget.spending_summary import (
    AgentSpending,
    DepartmentSpending,
    PeriodSpending,
)
from synthorg.budget.tracker import CostTracker

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)


def _rec(currency: str, *, cost: float = 0.10, agent: str = "alice") -> CostRecord:
    """Build a CostRecord with the given currency."""
    return CostRecord(
        agent_id=agent,
        task_id="task-1",
        provider="test-provider",
        model="test-model-001",
        input_tokens=100,
        output_tokens=50,
        cost=cost,
        currency=currency,
        timestamp=_NOW,
    )


class TestAssertCurrenciesMatch:
    """``assert_currencies_match`` raises on mixed input, returns code otherwise."""

    def test_empty_returns_none(self) -> None:
        assert assert_currencies_match(()) is None

    def test_single_currency_returns_code(self) -> None:
        assert assert_currencies_match(("EUR", "EUR")) == "EUR"

    def test_iterator_input_supported(self) -> None:
        """Generator inputs are exhausted exactly once."""
        assert assert_currencies_match(c for c in ("USD", "USD", "USD")) == "USD"

    def test_mixed_currency_raises(self) -> None:
        with pytest.raises(MixedCurrencyAggregationError) as exc:
            assert_currencies_match(("EUR", "USD"))
        assert exc.value.currencies == frozenset({"EUR", "USD"})

    def test_three_currencies_all_reported(self) -> None:
        with pytest.raises(MixedCurrencyAggregationError) as exc:
            assert_currencies_match(("EUR", "USD", "JPY"))
        assert exc.value.currencies == frozenset({"EUR", "USD", "JPY"})

    def test_agent_context_propagated(self) -> None:
        with pytest.raises(MixedCurrencyAggregationError) as exc:
            assert_currencies_match(
                ("EUR", "JPY"),
                agent_id="agent-9",
            )
        assert exc.value.agent_id == "agent-9"

    def test_task_and_project_context_propagated(self) -> None:
        with pytest.raises(MixedCurrencyAggregationError) as exc:
            assert_currencies_match(
                ("EUR", "USD"),
                task_id="task-1",
                project_id="proj-7",
            )
        assert exc.value.task_id == "task-1"
        assert exc.value.project_id == "proj-7"

    def test_record_currency_extraction_supported(self) -> None:
        """Idiom: ``assert_currencies_match(r.currency for r in records)``."""
        records = (_rec("EUR"), _rec("EUR"), _rec("EUR"))
        assert assert_currencies_match(r.currency for r in records) == "EUR"


class TestAggregateHelper:
    """``_aggregate`` applies the same-currency guard before summing."""

    def test_returns_currency_on_non_empty(self) -> None:
        result = _aggregate((_rec("EUR", cost=0.10), _rec("EUR", cost=0.20)))
        assert result.currency == "EUR"
        assert result.cost == pytest.approx(0.30)
        assert result.record_count == 2

    def test_empty_returns_none_currency(self) -> None:
        result = _aggregate(())
        assert result.currency is None
        assert result.cost == 0.0
        assert result.record_count == 0

    def test_mixed_raises_before_summing(self) -> None:
        with pytest.raises(MixedCurrencyAggregationError):
            _aggregate((_rec("EUR"), _rec("USD")))


class TestCostTrackerMixedCurrency:
    """CostTracker aggregator endpoints all guard mixed-currency streams."""

    async def test_get_total_cost_raises_on_mixed(self) -> None:
        tracker = CostTracker()
        await tracker.record(_rec("EUR", cost=0.10))
        await tracker.record(_rec("USD", cost=0.20))
        with pytest.raises(MixedCurrencyAggregationError):
            await tracker.get_total_cost()

    async def test_get_agent_cost_raises_on_mixed(self) -> None:
        tracker = CostTracker()
        await tracker.record(_rec("EUR", cost=0.10, agent="alice"))
        await tracker.record(_rec("JPY", cost=0.20, agent="alice"))
        with pytest.raises(MixedCurrencyAggregationError):
            await tracker.get_agent_cost("alice")

    async def test_build_summary_raises_on_mixed(self) -> None:
        tracker = CostTracker()
        await tracker.record(_rec("EUR", cost=0.10, agent="alice"))
        await tracker.record(_rec("USD", cost=0.20, agent="bob"))
        with pytest.raises(MixedCurrencyAggregationError):
            await tracker.build_summary(
                start=_NOW,
                end=datetime(2026, 4, 2, tzinfo=UTC),
            )

    async def test_provider_usage_raises_on_mixed(self) -> None:
        tracker = CostTracker()
        await tracker.record(_rec("EUR", cost=0.10))
        await tracker.record(_rec("USD", cost=0.20))
        from synthorg.core.types import NotBlankStr

        with pytest.raises(MixedCurrencyAggregationError):
            await tracker.get_provider_usage(NotBlankStr("test-provider"))

    async def test_department_rollup_raises_on_mixed(self) -> None:
        """Agents in the same department must agree on currency.

        ``build_summary`` would raise on the period-wide aggregation path
        before ever reaching ``_build_dept_spendings``, so we exercise the
        department-rollup branch directly with pre-built ``AgentSpending``
        rows carrying different currencies.  This guarantees coverage of
        the department-specific guard rather than the period-wide guard.
        """

        def resolver(aid: str) -> str | None:
            return {"alice": "Eng", "bob": "Eng"}.get(aid)

        tracker = CostTracker(department_resolver=resolver)
        agent_spendings = [
            AgentSpending(
                agent_id="alice",
                total_cost=0.10,
                currency="EUR",
                total_input_tokens=0,
                total_output_tokens=0,
                record_count=1,
            ),
            AgentSpending(
                agent_id="bob",
                total_cost=0.20,
                currency="USD",
                total_input_tokens=0,
                total_output_tokens=0,
                record_count=1,
            ),
        ]
        with pytest.raises(MixedCurrencyAggregationError) as exc_info:
            tracker._build_dept_spendings(agent_spendings)
        assert exc_info.value.currencies == frozenset({"EUR", "USD"})


class TestAgentSpendingCurrencyInvariant:
    """AgentSpending rejects record_count > 0 without a currency code."""

    def test_record_count_zero_currency_none_ok(self) -> None:
        spend = AgentSpending(agent_id="alice")
        assert spend.currency is None
        assert spend.record_count == 0

    def test_record_count_positive_requires_currency(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(
            ValidationError,
            match="currency is required when record_count > 0",
        ):
            AgentSpending(
                agent_id="alice",
                total_cost=5.0,
                record_count=3,
            )

    def test_record_count_positive_with_currency(self) -> None:
        spend = AgentSpending(
            agent_id="alice",
            total_cost=5.0,
            currency="EUR",
            record_count=3,
        )
        assert spend.currency == "EUR"


class TestAggregationProperties:
    """Property-based: arbitrary mixed-currency streams always raise."""

    @example(codes=["EUR", "USD"])
    @example(codes=["EUR", "USD", "JPY"])
    @example(codes=["EUR", "USD", "JPY", "GBP"])
    @example(codes=["USD", "EUR"] * 10)
    @given(
        codes=st.lists(
            st.sampled_from(["EUR", "USD", "JPY", "GBP"]),
            min_size=2,
            max_size=20,
        ).filter(lambda cs: len(set(cs)) >= 2),
    )
    def test_any_mixed_stream_raises(self, codes: list[str]) -> None:
        records = tuple(_rec(c, cost=0.01 * i) for i, c in enumerate(codes))
        with pytest.raises(MixedCurrencyAggregationError) as exc:
            _aggregate(records)
        assert exc.value.currencies == frozenset(codes)

    @example(code="EUR", count=1)
    @example(code="JPY", count=30)
    @given(
        code=st.sampled_from(["EUR", "USD", "JPY", "GBP", "CHF", "CNY"]),
        count=st.integers(min_value=1, max_value=30),
    )
    def test_any_uniform_stream_succeeds(self, code: str, count: int) -> None:
        records = tuple(_rec(code, cost=0.01 * i) for i in range(count))
        result = _aggregate(records)
        assert result.currency == code
        assert result.record_count == count


class TestSiblingSpendingCurrencyValidator:
    """``PeriodSpending`` and ``DepartmentSpending`` enforce the same invariant.

    ``_SpendingTotals._validate_currency_presence`` is inherited by every
    subclass; the ``AgentSpending`` suite above exercises one subclass.
    These tests pin the contract for the other two so a future regression
    on a sibling model cannot slip through.
    """

    def test_period_record_count_zero_currency_none_ok(self) -> None:
        period = PeriodSpending(
            start=datetime(2026, 4, 1, tzinfo=UTC),
            end=datetime(2026, 5, 1, tzinfo=UTC),
        )
        assert period.currency is None
        assert period.record_count == 0

    def test_period_record_count_positive_requires_currency(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(
            ValidationError,
            match="currency is required when record_count > 0",
        ):
            PeriodSpending(
                start=datetime(2026, 4, 1, tzinfo=UTC),
                end=datetime(2026, 5, 1, tzinfo=UTC),
                total_cost=10.0,
                record_count=5,
            )

    def test_period_record_count_positive_with_currency(self) -> None:
        period = PeriodSpending(
            start=datetime(2026, 4, 1, tzinfo=UTC),
            end=datetime(2026, 5, 1, tzinfo=UTC),
            total_cost=10.0,
            currency="EUR",
            record_count=5,
        )
        assert period.currency == "EUR"

    def test_department_record_count_zero_currency_none_ok(self) -> None:
        dept = DepartmentSpending(department_name="Engineering")
        assert dept.currency is None
        assert dept.record_count == 0

    def test_department_record_count_positive_requires_currency(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(
            ValidationError,
            match="currency is required when record_count > 0",
        ):
            DepartmentSpending(
                department_name="Engineering",
                total_cost=75.0,
                record_count=15,
            )

    def test_department_record_count_positive_with_currency(self) -> None:
        dept = DepartmentSpending(
            department_name="Engineering",
            total_cost=75.0,
            currency="EUR",
            record_count=15,
        )
        assert dept.currency == "EUR"
