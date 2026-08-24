"""Tests for the performance signal aggregator's observation scoping."""

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
    )


def _aggregator(
    snapshot: AgentPerformanceSnapshot,
) -> tuple[PerformanceSignalAggregator, AsyncMock]:
    get_snapshot = AsyncMock(return_value=snapshot)
    tracker = mock_of[PerformanceTracker](get_snapshot=get_snapshot)
    agg = PerformanceSignalAggregator(
        tracker=tracker,
        agent_ids_provider=lambda: ["agent-1"],
    )
    return agg, get_snapshot


class TestObservationScoping:
    async def test_forwards_since_and_until_to_snapshot(self) -> None:
        agg, get_snapshot = _aggregator(_snapshot("7d"))
        since = _UNTIL - timedelta(days=30)

        await agg.aggregate(since=since, until=_UNTIL)

        # The observation window bounds the snapshot: ``until`` is the
        # reference time and ``since`` clips the records that feed it.
        get_snapshot.assert_awaited_once_with("agent-1", now=_UNTIL, since=since)

    async def test_reports_every_rolling_window(self) -> None:
        # Rolling windows are multi-horizon context; a short observation
        # span must not drop the longer windows (a sub-day ``since`` is the
        # common case for the chief-of-staff monitor).
        agg, _ = _aggregator(_snapshot("7d", "30d", "90d"))
        since = _UNTIL - timedelta(seconds=60)

        summary = await agg.aggregate(since=since, until=_UNTIL)

        metric_names = {m.name for m in summary.metrics}
        assert {
            "success_rate_7d",
            "success_rate_30d",
            "success_rate_90d",
            "quality_7d",
            "quality_30d",
            "quality_90d",
        } <= metric_names
