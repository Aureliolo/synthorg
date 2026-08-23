"""Tests for the AnalyticsService and ReportsService facades."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.meta.analytics.service import AnalyticsService
from synthorg.meta.reports.service import ReportsService
from synthorg.meta.signal_models import (
    MetricSummary,
    OrgBudgetSummary,
    OrgCoordinationSummary,
    OrgErrorSummary,
    OrgEvolutionSummary,
    OrgPerformanceSummary,
    OrgSignalSnapshot,
    OrgTelemetrySummary,
    TrendDirection,
)

pytestmark = pytest.mark.unit


def _snapshot() -> OrgSignalSnapshot:
    return OrgSignalSnapshot(
        performance=OrgPerformanceSummary(
            avg_quality_score=8.0,
            avg_success_rate=0.9,
            avg_collaboration_score=7.5,
            agent_count=5,
            metrics=(
                MetricSummary(
                    name=NotBlankStr("throughput"),
                    value=42.0,
                    trend=TrendDirection.IMPROVING,
                    window_days=7,
                ),
            ),
        ),
        budget=OrgBudgetSummary(
            total_spend=100.0,
            productive_ratio=0.8,
            coordination_ratio=0.15,
            system_ratio=0.05,
            days_until_exhausted=42,
            forecast_confidence=0.7,
            orchestration_overhead=0.2,
        ),
        coordination=OrgCoordinationSummary(),
        errors=OrgErrorSummary(total_findings=7),
        evolution=OrgEvolutionSummary(total_proposals=3, approval_rate=0.5),
        telemetry=OrgTelemetrySummary(event_count=100, error_event_count=2),
    )


@pytest.fixture
def signals() -> AsyncMock:
    service = AsyncMock()
    snap = _snapshot()
    service.get_org_snapshot = AsyncMock(return_value=snap)
    service.get_performance = AsyncMock(return_value=snap.performance)
    service.get_budget = AsyncMock(return_value=snap.budget)
    return service


@pytest.fixture
def analytics(signals: AsyncMock) -> AnalyticsService:
    return AnalyticsService(signals=signals)


class TestAnalyticsService:
    async def test_overview_uses_snapshot_fields(
        self,
        analytics: AnalyticsService,
    ) -> None:
        now = datetime.now(UTC)
        result = await analytics.get_overview(
            since=now - timedelta(days=1),
            until=now,
        )
        assert result.avg_quality_score == 8.0
        assert result.total_spend == 100.0
        assert result.total_error_findings == 7

    async def test_trends_filters_by_metric_names(
        self,
        analytics: AnalyticsService,
    ) -> None:
        now = datetime.now(UTC)
        result = await analytics.get_trends(
            since=now - timedelta(days=7),
            until=now,
            metric_names=["throughput"],
        )
        assert len(result.metrics) == 1
        assert result.metrics[0].name == "throughput"

    async def test_trends_returns_all_when_no_filter(
        self,
        analytics: AnalyticsService,
    ) -> None:
        now = datetime.now(UTC)
        result = await analytics.get_trends(
            since=now - timedelta(days=7),
            until=now,
        )
        assert len(result.metrics) == 1

    async def test_forecast_projects_spend(
        self,
        analytics: AnalyticsService,
    ) -> None:
        now = datetime.now(UTC)
        result = await analytics.get_forecast(
            since=now - timedelta(days=1),
            until=now,
            horizon_days=30,
        )
        assert result.horizon_days == 30
        assert result.days_until_budget_exhausted == 42
        assert result.projected_spend >= 0.0

    async def test_forecast_rejects_invalid_horizon(
        self,
        analytics: AnalyticsService,
    ) -> None:
        now = datetime.now(UTC)
        with pytest.raises(ValueError, match="horizon_days"):
            await analytics.get_forecast(
                since=now - timedelta(days=1),
                until=now,
                horizon_days=0,
            )

    async def test_current_metrics_returns_flat_map(
        self,
        analytics: AnalyticsService,
    ) -> None:
        now = datetime.now(UTC)
        result = await analytics.get_current_metrics(
            since=now - timedelta(hours=1),
            until=now,
        )
        assert "budget.total_spend" in result.metrics

    async def test_history_returns_requested_samples(
        self,
        analytics: AnalyticsService,
    ) -> None:
        now = datetime.now(UTC)
        result = await analytics.get_metric_history(
            since=now - timedelta(days=1),
            until=now,
            metric_names=("budget.total_spend",),
            sample_count=3,
        )
        assert len(result.points) == 3


class TestReportsService:
    async def test_generate_and_get_roundtrip(
        self,
        analytics: AnalyticsService,
    ) -> None:
        service = ReportsService(analytics=analytics)
        report = await service.generate_report(
            template=NotBlankStr("org_overview"),
            author_id=NotBlankStr("author-1"),
        )
        fetched = await service.get_report(report.id)
        assert fetched is not None
        assert fetched.id == report.id
        assert fetched.template == "org_overview"

    async def test_generate_rejects_unknown_template(
        self,
        analytics: AnalyticsService,
    ) -> None:
        service = ReportsService(analytics=analytics)
        with pytest.raises(ValueError, match="Unknown report template"):
            await service.generate_report(
                template=NotBlankStr("does-not-exist"),
                author_id=NotBlankStr("a"),
            )

    async def test_list_paginates(
        self,
        analytics: AnalyticsService,
    ) -> None:
        service = ReportsService(analytics=analytics)
        for _ in range(5):
            await service.generate_report(
                template=NotBlankStr("org_overview"),
                author_id=NotBlankStr("a"),
            )
        page, total = await service.list_reports(offset=0, limit=3)
        assert total == 5
        assert len(page) == 3

    async def test_list_newest_first(
        self,
        analytics: AnalyticsService,
    ) -> None:
        service = ReportsService(analytics=analytics)
        first = await service.generate_report(
            template=NotBlankStr("org_overview"),
            author_id=NotBlankStr("a"),
        )
        second = await service.generate_report(
            template=NotBlankStr("metrics_snapshot"),
            author_id=NotBlankStr("a"),
        )
        page, _ = await service.list_reports()
        assert page[0].id == second.id
        assert page[1].id == first.id
