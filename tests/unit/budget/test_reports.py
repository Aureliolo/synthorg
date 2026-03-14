"""Tests for ReportGenerator service and report models."""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.budget.config import BudgetConfig
from synthorg.budget.reports import (
    ModelDistribution,
    PeriodComparison,
    ProviderDistribution,
    ReportGenerator,
    SpendingReport,
    TaskSpending,
)
from synthorg.budget.spending_summary import SpendingSummary
from synthorg.budget.tracker import CostTracker
from tests.unit.budget.conftest import make_cost_record

# ── Helpers ───────────────────────────────────────────────────────

_START = datetime(2026, 2, 1, tzinfo=UTC)
_END = datetime(2026, 3, 1, tzinfo=UTC)


def _make_report_generator(
    *,
    budget_config: BudgetConfig | None = None,
) -> tuple[ReportGenerator, CostTracker]:
    """Build a ReportGenerator with a fresh CostTracker."""
    bc = budget_config or BudgetConfig(total_monthly=100.0)
    tracker = CostTracker(budget_config=bc)
    gen = ReportGenerator(cost_tracker=tracker, budget_config=bc)
    return gen, tracker


# ── Report Model Tests ────────────────────────────────────────────


@pytest.mark.unit
class TestTaskSpending:
    def test_construction(self) -> None:
        ts = TaskSpending(
            task_id="task-001",
            total_cost_usd=5.0,
            total_tokens=10000,
            record_count=10,
        )
        assert ts.task_id == "task-001"
        assert ts.total_cost_usd == 5.0

    def test_frozen(self) -> None:
        ts = TaskSpending(
            task_id="task-001",
            total_cost_usd=5.0,
            total_tokens=10000,
            record_count=10,
        )
        with pytest.raises(Exception):  # noqa: B017, PT011
            ts.task_id = "other"  # type: ignore[misc]


@pytest.mark.unit
class TestProviderDistribution:
    def test_construction(self) -> None:
        pd = ProviderDistribution(
            provider="test-provider",
            total_cost_usd=50.0,
            record_count=100,
            percentage_of_total=100.0,
        )
        assert pd.provider == "test-provider"
        assert pd.percentage_of_total == 100.0


@pytest.mark.unit
class TestModelDistribution:
    def test_construction(self) -> None:
        md = ModelDistribution(
            model="test-model-001",
            provider="test-provider",
            total_cost_usd=50.0,
            record_count=100,
            percentage_of_total=50.0,
        )
        assert md.model == "test-model-001"


@pytest.mark.unit
class TestPeriodComparison:
    def test_cost_increase(self) -> None:
        pc = PeriodComparison(
            current_period_cost=100.0,
            previous_period_cost=80.0,
        )
        assert pc.cost_change_usd == 20.0
        assert pc.cost_change_percent == 25.0

    def test_cost_decrease(self) -> None:
        pc = PeriodComparison(
            current_period_cost=60.0,
            previous_period_cost=80.0,
        )
        assert pc.cost_change_usd == -20.0
        assert pc.cost_change_percent == -25.0

    def test_no_previous_data_percent_is_none(self) -> None:
        pc = PeriodComparison(
            current_period_cost=50.0,
            previous_period_cost=0.0,
        )
        assert pc.cost_change_usd == 50.0
        assert pc.cost_change_percent is None

    def test_equal_periods(self) -> None:
        pc = PeriodComparison(
            current_period_cost=50.0,
            previous_period_cost=50.0,
        )
        assert pc.cost_change_usd == 0.0
        assert pc.cost_change_percent == 0.0


# ── ReportGenerator Tests ─────────────────────────────────────────


@pytest.mark.unit
class TestReportGenerator:
    async def test_init(self) -> None:
        gen, _ = _make_report_generator()
        assert gen._cost_tracker is not None
        assert gen._budget_config is not None

    async def test_generate_report_no_records(self) -> None:
        gen, _ = _make_report_generator()
        report = await gen.generate_report(start=_START, end=_END)
        assert report.by_task == ()
        assert report.by_provider == ()
        assert report.by_model == ()
        assert report.summary.period.total_cost_usd == 0.0

    async def test_generate_report_multiple_agents_tasks(self) -> None:
        gen, tracker = _make_report_generator()

        await tracker.record(
            make_cost_record(
                agent_id="alice",
                task_id="task-a",
                cost_usd=3.0,
                timestamp=_START + timedelta(hours=1),
            ),
        )
        await tracker.record(
            make_cost_record(
                agent_id="bob",
                task_id="task-b",
                cost_usd=5.0,
                timestamp=_START + timedelta(hours=2),
            ),
        )
        await tracker.record(
            make_cost_record(
                agent_id="alice",
                task_id="task-a",
                cost_usd=2.0,
                timestamp=_START + timedelta(hours=3),
            ),
        )

        report = await gen.generate_report(start=_START, end=_END)
        assert report.summary.period.total_cost_usd == 10.0
        assert len(report.by_task) == 2

        # task-a has 5.0, task-b has 5.0
        task_a = next(t for t in report.by_task if t.task_id == "task-a")
        assert task_a.total_cost_usd == 5.0
        assert task_a.record_count == 2

    async def test_provider_distribution_percentages(self) -> None:
        gen, tracker = _make_report_generator()

        await tracker.record(
            make_cost_record(
                provider="provider-a",
                cost_usd=3.0,
                timestamp=_START + timedelta(hours=1),
            ),
        )
        await tracker.record(
            make_cost_record(
                provider="provider-b",
                cost_usd=7.0,
                timestamp=_START + timedelta(hours=2),
            ),
        )

        report = await gen.generate_report(start=_START, end=_END)
        assert len(report.by_provider) == 2
        total_pct = sum(p.percentage_of_total for p in report.by_provider)
        assert abs(total_pct - 100.0) < 0.01

    async def test_model_distribution(self) -> None:
        gen, tracker = _make_report_generator()

        await tracker.record(
            make_cost_record(
                model="model-a",
                cost_usd=4.0,
                timestamp=_START + timedelta(hours=1),
            ),
        )
        await tracker.record(
            make_cost_record(
                model="model-b",
                cost_usd=6.0,
                timestamp=_START + timedelta(hours=2),
            ),
        )

        report = await gen.generate_report(start=_START, end=_END)
        assert len(report.by_model) == 2
        model_a = next(m for m in report.by_model if m.model == "model-a")
        assert model_a.total_cost_usd == 4.0

    async def test_period_comparison_cost_increase(self) -> None:
        gen, tracker = _make_report_generator()

        # Previous period data
        prev_start = _START - (_END - _START)
        await tracker.record(
            make_cost_record(
                cost_usd=5.0,
                timestamp=prev_start + timedelta(hours=1),
            ),
        )
        # Current period data
        await tracker.record(
            make_cost_record(
                cost_usd=8.0,
                timestamp=_START + timedelta(hours=1),
            ),
        )

        report = await gen.generate_report(start=_START, end=_END)
        assert report.period_comparison is not None
        assert report.period_comparison.current_period_cost == 8.0
        assert report.period_comparison.previous_period_cost == 5.0
        assert report.period_comparison.cost_change_usd == 3.0
        assert report.period_comparison.cost_change_percent == 60.0

    async def test_no_prior_data_no_comparison(self) -> None:
        gen, _ = _make_report_generator()
        report = await gen.generate_report(start=_START, end=_END)
        # Both periods have zero cost → no comparison
        assert report.period_comparison is None

    async def test_top_n_agents(self) -> None:
        gen, tracker = _make_report_generator()

        for i, agent in enumerate(["alice", "bob", "carol", "dave", "eve"]):
            await tracker.record(
                make_cost_record(
                    agent_id=agent,
                    cost_usd=float(i + 1),
                    timestamp=_START + timedelta(hours=i + 1),
                ),
            )

        report = await gen.generate_report(
            start=_START,
            end=_END,
            top_n=3,
        )
        assert len(report.top_agents_by_cost) == 3
        # Sorted descending
        assert report.top_agents_by_cost[0][0] == "eve"
        assert report.top_agents_by_cost[0][1] == 5.0

    async def test_top_n_tasks(self) -> None:
        gen, tracker = _make_report_generator()

        for i, task in enumerate(["t1", "t2", "t3", "t4"]):
            await tracker.record(
                make_cost_record(
                    task_id=task,
                    cost_usd=float(i + 1) * 2,
                    timestamp=_START + timedelta(hours=i + 1),
                ),
            )

        report = await gen.generate_report(
            start=_START,
            end=_END,
            top_n=2,
        )
        assert len(report.top_tasks_by_cost) == 2
        assert report.top_tasks_by_cost[0][0] == "t4"

    async def test_top_n_validation(self) -> None:
        gen, _ = _make_report_generator()
        with pytest.raises(ValueError, match="top_n must be >= 1"):
            await gen.generate_report(start=_START, end=_END, top_n=0)

    async def test_period_comparison_cost_decrease(self) -> None:
        gen, tracker = _make_report_generator()

        prev_start = _START - (_END - _START)
        await tracker.record(
            make_cost_record(
                cost_usd=10.0,
                timestamp=prev_start + timedelta(hours=1),
            ),
        )
        await tracker.record(
            make_cost_record(
                cost_usd=3.0,
                timestamp=_START + timedelta(hours=1),
            ),
        )

        report = await gen.generate_report(start=_START, end=_END)
        assert report.period_comparison is not None
        assert report.period_comparison.cost_change_usd == -7.0
        assert report.period_comparison.cost_change_percent is not None
        assert report.period_comparison.cost_change_percent < 0

    async def test_skip_period_comparison(self) -> None:
        gen, tracker = _make_report_generator()

        await tracker.record(
            make_cost_record(
                cost_usd=5.0,
                timestamp=_START + timedelta(hours=1),
            ),
        )

        report = await gen.generate_report(
            start=_START,
            end=_END,
            include_period_comparison=False,
        )
        assert report.period_comparison is None

    async def test_start_after_end_rejected(self) -> None:
        gen, _ = _make_report_generator()
        with pytest.raises(ValueError, match=r"start .* must be before end"):
            await gen.generate_report(start=_END, end=_START)


# ── SpendingReport Validator Tests ───────────────────────────────


def _make_summary() -> SpendingSummary:
    """Build a minimal SpendingSummary for validator tests."""
    from synthorg.budget.spending_summary import PeriodSpending

    return SpendingSummary(
        period=PeriodSpending(
            start=_START,
            end=_END,
            total_cost_usd=0.0,
        ),
    )


@pytest.mark.unit
class TestSpendingReportValidators:
    def test_agents_sorted_descending_accepted(self) -> None:
        report = SpendingReport(
            summary=_make_summary(),
            top_agents_by_cost=(("eve", 5.0), ("bob", 3.0), ("alice", 1.0)),
            generated_at=_START,
        )
        assert len(report.top_agents_by_cost) == 3

    def test_agents_unsorted_rejected(self) -> None:
        with pytest.raises(ValueError, match="top_agents_by_cost must be sorted"):
            SpendingReport(
                summary=_make_summary(),
                top_agents_by_cost=(("alice", 1.0), ("bob", 5.0)),
                generated_at=_START,
            )

    def test_tasks_sorted_descending_accepted(self) -> None:
        report = SpendingReport(
            summary=_make_summary(),
            top_tasks_by_cost=(("t2", 8.0), ("t1", 2.0)),
            generated_at=_START,
        )
        assert len(report.top_tasks_by_cost) == 2

    def test_tasks_unsorted_rejected(self) -> None:
        with pytest.raises(ValueError, match="top_tasks_by_cost must be sorted"):
            SpendingReport(
                summary=_make_summary(),
                top_tasks_by_cost=(("t1", 2.0), ("t2", 8.0)),
                generated_at=_START,
            )
