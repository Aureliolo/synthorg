"""Tests for the performance summary extraction pure function."""

from datetime import UTC, datetime

import pytest

from synthorg.hr.enums import TrendDirection
from synthorg.hr.performance.models import (
    AgentPerformanceSnapshot,
    TrendResult,
    WindowMetrics,
)
from synthorg.hr.performance.summary import (
    extract_performance_summary,
)

_NOW = datetime(2026, 3, 24, 12, 0, 0, tzinfo=UTC)


def _make_window(
    *,
    window_size: str = "7d",
    tasks_completed: int = 5,
    tasks_failed: int = 1,
    success_rate: float | None = 0.83,
    avg_cost_per_task: float | None = 0.05,
    avg_completion_time_seconds: float | None = 120.0,
    avg_quality_score: float | None = 7.5,
    currency: str = "EUR",
) -> WindowMetrics:
    return WindowMetrics(
        window_size=window_size,
        data_point_count=tasks_completed + tasks_failed,
        tasks_completed=tasks_completed,
        tasks_failed=tasks_failed,
        success_rate=success_rate,
        avg_cost_per_task=avg_cost_per_task,
        avg_completion_time_seconds=avg_completion_time_seconds,
        avg_quality_score=avg_quality_score,
        currency=currency,
    )


def _make_trend(
    *,
    metric_name: str = "success_rate",
    window_size: str = "30d",
    direction: TrendDirection = TrendDirection.IMPROVING,
    slope: float = 0.01,
    data_point_count: int = 10,
) -> TrendResult:
    return TrendResult(
        metric_name=metric_name,
        window_size=window_size,
        direction=direction,
        slope=slope,
        data_point_count=data_point_count,
    )


def _make_snapshot(
    *,
    windows: tuple[WindowMetrics, ...] = (),
    trends: tuple[TrendResult, ...] = (),
    quality_score: float | None = None,
) -> AgentPerformanceSnapshot:
    return AgentPerformanceSnapshot(
        agent_id="agent-001",
        computed_at=_NOW,
        windows=windows,
        trends=trends,
        overall_quality_score=quality_score,
    )


@pytest.mark.unit
class TestExtractPerformanceSummary:
    def test_full_snapshot(self) -> None:
        w7 = _make_window(window_size="7d", tasks_completed=3, tasks_failed=1)
        w30 = _make_window(
            window_size="30d",
            tasks_completed=12,
            tasks_failed=3,
            success_rate=0.8,
            avg_cost_per_task=0.04,
            avg_completion_time_seconds=90.0,
        )
        trend = _make_trend(direction=TrendDirection.IMPROVING)
        snapshot = _make_snapshot(
            windows=(w7, w30),
            trends=(trend,),
            quality_score=7.5,
        )

        summary = extract_performance_summary(snapshot, "alice")

        assert summary.agent_name == "alice"
        assert summary.tasks_completed_7d == 3
        assert summary.tasks_completed_30d == 12
        # Best available = max tasks_completed across windows (30d has 12)
        assert summary.tasks_completed_total == 12
        assert summary.avg_completion_time_seconds == pytest.approx(90.0)
        assert summary.success_rate_percent == pytest.approx(80.0)
        assert summary.cost_per_task == pytest.approx(0.04)
        assert summary.quality_score == pytest.approx(7.5)
        assert summary.trend_direction == TrendDirection.IMPROVING
        assert len(summary.windows) == 2
        assert len(summary.trends) == 1

    def test_empty_snapshot(self) -> None:
        snapshot = _make_snapshot()

        summary = extract_performance_summary(snapshot, "bob")

        assert summary.agent_name == "bob"
        assert summary.tasks_completed_total == 0
        assert summary.tasks_completed_7d == 0
        assert summary.tasks_completed_30d == 0
        assert summary.avg_completion_time_seconds is None
        assert summary.success_rate_percent is None
        assert summary.cost_per_task is None
        assert summary.quality_score is None
        assert summary.trend_direction == TrendDirection.INSUFFICIENT_DATA

    def test_only_7d_window(self) -> None:
        w7 = _make_window(
            window_size="7d",
            tasks_completed=5,
            tasks_failed=0,
            success_rate=1.0,
            avg_cost_per_task=0.10,
            avg_completion_time_seconds=60.0,
        )
        snapshot = _make_snapshot(windows=(w7,))

        summary = extract_performance_summary(snapshot, "carol")

        assert summary.tasks_completed_7d == 5
        assert summary.tasks_completed_30d == 0
        # Falls back to 7d window for metrics
        assert summary.success_rate_percent == pytest.approx(100.0)
        assert summary.cost_per_task == pytest.approx(0.10)
        assert summary.avg_completion_time_seconds == pytest.approx(60.0)

    def test_trend_prefers_success_rate(self) -> None:
        cost_trend = _make_trend(
            metric_name="cost",
            direction=TrendDirection.DECLINING,
        )
        success_trend = _make_trend(
            metric_name="success_rate",
            direction=TrendDirection.STABLE,
        )
        snapshot = _make_snapshot(trends=(cost_trend, success_trend))

        summary = extract_performance_summary(snapshot, "dave")

        assert summary.trend_direction == TrendDirection.STABLE

    def test_trend_falls_back_to_first(self) -> None:
        cost_trend = _make_trend(
            metric_name="cost",
            direction=TrendDirection.DECLINING,
        )
        snapshot = _make_snapshot(trends=(cost_trend,))

        summary = extract_performance_summary(snapshot, "eve")

        assert summary.trend_direction == TrendDirection.DECLINING

    def test_success_rate_none_produces_none_percent(self) -> None:
        w30 = _make_window(
            window_size="30d",
            tasks_completed=0,
            tasks_failed=0,
            success_rate=None,
        )
        snapshot = _make_snapshot(windows=(w30,))

        summary = extract_performance_summary(snapshot, "frank")

        assert summary.success_rate_percent is None

    def test_only_30d_window(self) -> None:
        w30 = _make_window(
            window_size="30d",
            tasks_completed=10,
            tasks_failed=2,
            success_rate=0.83,
            avg_cost_per_task=0.06,
            avg_completion_time_seconds=75.0,
        )
        snapshot = _make_snapshot(windows=(w30,))

        summary = extract_performance_summary(snapshot, "grace")

        assert summary.tasks_completed_7d == 0
        assert summary.tasks_completed_30d == 10
        assert summary.tasks_completed_total == 10
        assert summary.success_rate_percent == pytest.approx(83.0)
        assert summary.cost_per_task == pytest.approx(0.06)
        assert summary.avg_completion_time_seconds == pytest.approx(75.0)

    def test_7d_only_allows_zero_30d(self) -> None:
        """When only a 7d window exists, 30d count is 0 which is valid."""
        w7 = _make_window(
            window_size="7d",
            tasks_completed=5,
            tasks_failed=0,
        )
        snapshot = _make_snapshot(windows=(w7,))

        summary = extract_performance_summary(snapshot, "heidi")

        assert summary.tasks_completed_7d == 5
        assert summary.tasks_completed_30d == 0
        assert summary.tasks_completed_total == 5
