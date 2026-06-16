"""Tests for the AutomatedReportService."""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.budget.automated_reports import (
    AutomatedReportService,
    _build_performance_report,
    compute_period_range,
)
from synthorg.budget.config import BudgetConfig
from synthorg.budget.errors import MixedCurrencyAggregationError
from synthorg.budget.report_config import ReportPeriod
from synthorg.budget.reports import ReportGenerator
from synthorg.budget.risk_config import RiskBudgetConfig
from synthorg.budget.risk_tracker import RiskTracker
from synthorg.budget.tracker import CostTracker
from tests.unit.budget.conftest import make_cost_record, make_risk_record


def _make_service(
    *,
    with_risk_tracker: bool = False,
) -> AutomatedReportService:
    budget_config = BudgetConfig()
    cost_tracker = CostTracker(budget_config=budget_config)
    report_generator = ReportGenerator(
        cost_tracker=cost_tracker,
        budget_config=budget_config,
    )
    risk_tracker: RiskTracker | None = None
    if with_risk_tracker:
        risk_tracker = RiskTracker(
            risk_budget_config=RiskBudgetConfig(enabled=True),
        )
    return AutomatedReportService(
        report_generator=report_generator,
        cost_tracker=cost_tracker,
        risk_tracker=risk_tracker,
    )


@pytest.mark.unit
class TestBuildPerformanceReportCurrency:
    """``_build_performance_report`` rejects mixed-currency cost input."""

    def test_uniform_currency_aggregates(self) -> None:
        now = datetime(2026, 4, 4, 12, 0, tzinfo=UTC)
        cost_records = (
            make_cost_record(agent_id="alice", cost=0.10, currency="USD"),
            make_cost_record(agent_id="bob", cost=0.20, currency="USD"),
        )
        report = _build_performance_report((), cost_records, (), now)
        assert report.generated_at == now

    def test_mixed_currency_raises(self) -> None:
        """Two agents billed in different currencies must not blend into
        a single meaningless ``total_cost``."""
        now = datetime(2026, 4, 4, 12, 0, tzinfo=UTC)
        cost_records = (
            make_cost_record(agent_id="alice", cost=0.10, currency="USD"),
            make_cost_record(agent_id="bob", cost=0.20, currency="EUR"),
        )
        with pytest.raises(MixedCurrencyAggregationError):
            _build_performance_report((), cost_records, (), now)


@pytest.mark.unit
class TestComputePeriodRange:
    """Tests for period range computation."""

    def test_daily_period(self) -> None:
        ref = datetime(2026, 4, 4, 12, 0, tzinfo=UTC)
        start, end = compute_period_range(ReportPeriod.DAILY, ref)
        assert start == datetime(2026, 4, 3, 0, 0, tzinfo=UTC)
        assert end == datetime(2026, 4, 4, 0, 0, tzinfo=UTC)

    def test_weekly_period(self) -> None:
        # 2026-04-04 is a Saturday. Current Monday = 2026-03-30.
        ref = datetime(2026, 4, 4, 12, 0, tzinfo=UTC)
        start, end = compute_period_range(ReportPeriod.WEEKLY, ref)
        # Previous Monday = 2026-03-23, current Monday = 2026-03-30.
        assert start == datetime(2026, 3, 23, 0, 0, tzinfo=UTC)
        assert end == datetime(2026, 3, 30, 0, 0, tzinfo=UTC)

    def test_monthly_period(self) -> None:
        ref = datetime(2026, 4, 15, 12, 0, tzinfo=UTC)
        start, end = compute_period_range(ReportPeriod.MONTHLY, ref)
        assert start == datetime(2026, 3, 1, 0, 0, tzinfo=UTC)
        assert end == datetime(2026, 4, 1, 0, 0, tzinfo=UTC)

    def test_monthly_period_january(self) -> None:
        ref = datetime(2026, 1, 15, 12, 0, tzinfo=UTC)
        start, end = compute_period_range(ReportPeriod.MONTHLY, ref)
        assert start == datetime(2025, 12, 1, 0, 0, tzinfo=UTC)
        assert end == datetime(2026, 1, 1, 0, 0, tzinfo=UTC)

    def test_period_start_before_end(self) -> None:
        ref = datetime.now(UTC)
        for period in ReportPeriod:
            start, end = compute_period_range(period, ref)
            assert start < end, f"{period}: start={start} >= end={end}"


@pytest.mark.unit
class TestGenerateComprehensiveReport:
    """Tests for comprehensive report generation."""

    async def test_generates_with_empty_data(self) -> None:
        service = _make_service()
        report = await service.generate_comprehensive_report(
            period=ReportPeriod.DAILY,
        )
        assert report.period == ReportPeriod.DAILY
        assert report.spending is not None
        assert report.performance is not None
        assert report.task_completion is not None
        assert report.risk_trends is not None

    async def test_risk_trends_empty_without_tracker(self) -> None:
        service = _make_service(with_risk_tracker=False)
        report = await service.generate_comprehensive_report(
            period=ReportPeriod.DAILY,
        )
        assert report.risk_trends is not None
        assert report.risk_trends.total_risk_units == 0.0

    async def test_start_end_populated(self) -> None:
        service = _make_service()
        report = await service.generate_comprehensive_report(
            period=ReportPeriod.DAILY,
        )
        assert report.start < report.end


@pytest.mark.unit
class TestGenerateRiskTrendsReport:
    """Tests for risk trends report generation."""

    async def test_empty_without_tracker(self) -> None:
        service = _make_service(with_risk_tracker=False)
        now = datetime.now(UTC)
        report = await service.generate_risk_trends_report(
            start=now - timedelta(days=1),
            end=now,
        )
        assert report.total_risk_units == 0.0
        assert report.risk_by_agent == ()

    async def test_with_risk_data(self) -> None:
        budget_config = BudgetConfig()
        cost_tracker = CostTracker(budget_config=budget_config)
        report_generator = ReportGenerator(
            cost_tracker=cost_tracker,
            budget_config=budget_config,
        )
        risk_tracker = RiskTracker(
            risk_budget_config=RiskBudgetConfig(enabled=True),
        )
        service = AutomatedReportService(
            report_generator=report_generator,
            cost_tracker=cost_tracker,
            risk_tracker=risk_tracker,
        )

        now = datetime.now(UTC)
        await risk_tracker.record(
            make_risk_record(agent_id="a", risk_units=0.5, timestamp=now),
        )
        await risk_tracker.record(
            make_risk_record(agent_id="b", risk_units=0.3, timestamp=now),
        )

        report = await service.generate_risk_trends_report(
            start=now - timedelta(hours=1),
            end=now + timedelta(hours=1),
        )
        assert report.total_risk_units == pytest.approx(0.8)
        assert len(report.risk_by_agent) == 2
        # Sorted descending.
        assert report.risk_by_agent[0][0] == "a"
        assert report.risk_by_agent[1][0] == "b"


@pytest.mark.unit
class TestGenerateSpendingReport:
    """Tests for spending report delegation."""

    async def test_delegates_to_report_generator(self) -> None:
        service = _make_service()
        now = datetime.now(UTC)
        report = await service.generate_spending_report(
            start=now - timedelta(days=1),
            end=now,
        )
        assert report.generated_at is not None


@pytest.mark.unit
class TestGeneratePerformanceReport:
    """Tests for performance report generation."""

    async def test_empty_without_tracker(self) -> None:
        service = _make_service()
        now = datetime.now(UTC)
        report = await service.generate_performance_report(
            start=now - timedelta(days=1),
            end=now,
        )
        assert report.total_tasks_completed == 0
        assert report.agent_snapshots == ()
