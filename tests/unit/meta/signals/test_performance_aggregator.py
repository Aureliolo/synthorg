"""Tests for the performance signal aggregator's window scoping."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from synthorg.hr.performance.models import AgentPerformanceSnapshot, WindowMetrics
from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.meta.signals.performance import PerformanceSignalAggregator
from tests._shared import mock_of

pytestmark = pytest.mark.unit

_UNTIL = datetime(2026, 6, 20, tzinfo=UTC)


def _window(size: str) -> WindowMetrics:
    return WindowMetrics(
        window_size=size,
        data_point_count=5,
        tasks_completed=4,
        tasks_failed=1,
        avg_quality_score=8.0,
        success_rate=0.8,
    )


def _snapshot(*sizes: str) -> AgentPerformanceSnapshot:
    return AgentPerformanceSnapshot(
        agent_id="agent-1",
        computed_at=_UNTIL,
        windows=tuple(_window(s) for s in sizes),
        overall_quality_score=8.0,
        overall_collaboration_score=7.0,
    )


def _aggregator(snapshot: AgentPerformanceSnapshot) -> PerformanceSignalAggregator:
    tracker = mock_of[PerformanceTracker](
        get_snapshot=AsyncMock(return_value=snapshot),
    )
    return PerformanceSignalAggregator(
        tracker=tracker,
        agent_ids_provider=lambda: ["agent-1"],
    )


class TestWindowFits:
    @pytest.mark.parametrize(
        ("size", "requested_days", "expected"),
        [
            ("7d", 30, True),
            ("30d", 30, True),
            ("90d", 30, False),
            ("7d", 0, False),
            # An unparseable label cannot be bounded, so it is included.
            ("garbage", 5, True),
        ],
    )
    def test_window_fits(
        self,
        size: str,
        requested_days: int,
        expected: bool,
    ) -> None:
        fits = PerformanceSignalAggregator._window_fits(size, requested_days)
        assert fits is expected


class TestSinceScoping:
    async def test_oversized_windows_excluded(self) -> None:
        agg = _aggregator(_snapshot("7d", "30d", "90d"))
        # A 10-day observation span admits only the 7d rolling window.
        since = _UNTIL - timedelta(days=10)
        summary = await agg.aggregate(since=since, until=_UNTIL)
        metric_names = {m.name for m in summary.metrics}
        assert "success_rate_7d" in metric_names
        assert "quality_7d" in metric_names
        assert "success_rate_30d" not in metric_names
        assert "quality_90d" not in metric_names

    async def test_wide_span_includes_all_windows(self) -> None:
        agg = _aggregator(_snapshot("7d", "30d", "90d"))
        since = _UNTIL - timedelta(days=365)
        summary = await agg.aggregate(since=since, until=_UNTIL)
        metric_names = {m.name for m in summary.metrics}
        assert {"success_rate_7d", "success_rate_30d", "success_rate_90d"} <= (
            metric_names
        )
