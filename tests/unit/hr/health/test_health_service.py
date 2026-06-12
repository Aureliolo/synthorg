"""Unit tests for :class:`AgentHealthService`."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.hr.health.service import AgentHealthService
from synthorg.hr.performance.models import (
    AgentPerformanceSnapshot,
    WindowMetrics,
)
from synthorg.hr.performance.tracker import PerformanceTracker
from tests._shared import mock_of

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 4, 24, 12, 0, tzinfo=UTC)


def _window(
    *,
    window_size: str,
    success_rate: float | None,
    completed: int,
    failed: int,
) -> WindowMetrics:
    return WindowMetrics(
        window_size=NotBlankStr(window_size),
        data_point_count=completed + failed,
        tasks_completed=completed,
        tasks_failed=failed,
        success_rate=success_rate,
    )


def _snapshot(windows: tuple[WindowMetrics, ...]) -> AgentPerformanceSnapshot:
    return AgentPerformanceSnapshot(
        agent_id=NotBlankStr("agent-xyz"),
        computed_at=_NOW,
        windows=windows,
    )


def _tracker(snapshot: AgentPerformanceSnapshot) -> PerformanceTracker:
    """Autospec ``PerformanceTracker`` whose ``get_snapshot`` returns *snapshot*."""
    tracker: PerformanceTracker = mock_of[PerformanceTracker](
        get_snapshot=AsyncMock(return_value=snapshot),
    )
    return tracker


class TestVerdict:
    """Status mapping from recent success rate."""

    async def test_healthy_when_success_rate_above_threshold(self) -> None:
        snap = _snapshot(
            (_window(window_size="7d", success_rate=0.9, completed=9, failed=1),),
        )
        service = AgentHealthService(
            performance_tracker=_tracker(snap),
        )

        report = await service.get_agent_health(NotBlankStr("agent-xyz"))

        assert report.status == "healthy"
        assert report.recent_window == "7d"
        assert report.recent_success_rate == 0.9
        assert report.recent_task_count == 10
        assert report.recent_failed_count == 1

    @pytest.mark.parametrize("rate", [0.79, 0.6, 0.51])
    async def test_degraded_band(self, rate: float) -> None:
        total = 100
        failed = round(total * (1 - rate))
        completed = total - failed
        snap = _snapshot(
            (
                _window(
                    window_size="7d",
                    success_rate=rate,
                    completed=completed,
                    failed=failed,
                ),
            ),
        )
        service = AgentHealthService(
            performance_tracker=_tracker(snap),
        )

        report = await service.get_agent_health(NotBlankStr("agent-xyz"))

        assert report.status == "degraded"

    async def test_exact_healthy_threshold_is_healthy(self) -> None:
        """At exactly 0.8 the verdict stays healthy (inclusive lower bound)."""
        snap = _snapshot(
            (_window(window_size="7d", success_rate=0.8, completed=8, failed=2),),
        )
        service = AgentHealthService(
            performance_tracker=_tracker(snap),
        )

        report = await service.get_agent_health(NotBlankStr("agent-xyz"))

        assert report.status == "healthy"

    @pytest.mark.parametrize("rate", [0.5, 0.3, 0.0])
    async def test_unavailable_band(self, rate: float) -> None:
        total = 100
        failed = round(total * (1 - rate))
        completed = total - failed
        snap = _snapshot(
            (
                _window(
                    window_size="7d",
                    success_rate=rate,
                    completed=completed,
                    failed=failed,
                ),
            ),
        )
        service = AgentHealthService(
            performance_tracker=_tracker(snap),
        )

        report = await service.get_agent_health(NotBlankStr("agent-xyz"))

        assert report.status == "unavailable"


class TestNoSignal:
    """Windows with zero data default to healthy with no signal."""

    async def test_no_windows_reports_healthy_none(self) -> None:
        snap = _snapshot(())
        service = AgentHealthService(
            performance_tracker=_tracker(snap),
        )

        report = await service.get_agent_health(NotBlankStr("agent-xyz"))

        assert report.status == "healthy"
        assert report.recent_window is None
        assert report.recent_success_rate is None
        assert report.recent_task_count == 0

    async def test_all_windows_empty_reports_healthy(self) -> None:
        snap = _snapshot(
            (
                _window(
                    window_size="7d",
                    success_rate=None,
                    completed=0,
                    failed=0,
                ),
                _window(
                    window_size="30d",
                    success_rate=None,
                    completed=0,
                    failed=0,
                ),
            ),
        )
        service = AgentHealthService(
            performance_tracker=_tracker(snap),
        )

        report = await service.get_agent_health(NotBlankStr("agent-xyz"))

        assert report.status == "healthy"
        assert report.recent_window is None
        assert report.recent_success_rate is None


class TestWindowPicking:
    """When multiple windows have data, the tightest populated wins.

    Picking the shortest populated horizon (tracker order is
    shortest-to-longest) keeps the verdict responsive to fresh dips;
    a recent regression would otherwise be averaged out against
    months of older successes in the 30d / 90d horizons.
    """

    async def test_selects_tightest_populated_window(self) -> None:
        windows = (
            _window(
                window_size="7d",
                success_rate=0.9,
                completed=2,
                failed=0,
            ),
            _window(
                window_size="30d",
                success_rate=0.4,
                completed=50,
                failed=75,
            ),
        )
        snap = _snapshot(windows)
        service = AgentHealthService(
            performance_tracker=_tracker(snap),
        )

        report = await service.get_agent_health(NotBlankStr("agent-xyz"))

        # 7d is populated and tighter -- even though 30d has more data
        # points, 7d is the one that reacts fastest to fresh
        # regressions.
        assert report.recent_window == "7d"
        assert report.status == "healthy"

    async def test_skips_empty_windows_even_if_listed_first(self) -> None:
        windows = (
            _window(
                window_size="7d",
                success_rate=None,
                completed=0,
                failed=0,
            ),
            _window(
                window_size="30d",
                success_rate=0.95,
                completed=19,
                failed=1,
            ),
        )
        snap = _snapshot(windows)
        service = AgentHealthService(
            performance_tracker=_tracker(snap),
        )

        report = await service.get_agent_health(NotBlankStr("agent-xyz"))

        assert report.recent_window == "30d"
        assert report.status == "healthy"
