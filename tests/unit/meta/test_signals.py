"""Unit tests for meta-loop signal aggregation."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.hr.performance.tracker import PerformanceTracker
from synthorg.hr.scaling.service import ScalingService
from synthorg.meta.models import (
    OrgBudgetSummary,
    OrgCoordinationSummary,
    OrgErrorSummary,
    OrgEvolutionSummary,
    OrgPerformanceSummary,
    OrgScalingSummary,
    OrgSignalSnapshot,
    OrgTelemetrySummary,
)
from synthorg.meta.signals.budget import BudgetSignalAggregator
from synthorg.meta.signals.coordination import (
    CoordinationSignalAggregator,
)
from synthorg.meta.signals.errors import ErrorSignalAggregator
from synthorg.meta.signals.evolution import EvolutionSignalAggregator
from synthorg.meta.signals.performance import (
    PerformanceSignalAggregator,
)
from synthorg.meta.signals.scaling import ScalingSignalAggregator
from synthorg.meta.signals.snapshot import SnapshotBuilder
from synthorg.meta.signals.telemetry import TelemetrySignalAggregator
from tests._shared import mock_of

pytestmark = pytest.mark.unit


# ── Helpers ────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(UTC)


def _week_ago() -> datetime:
    return _now() - timedelta(days=7)


def _make_mock_tracker(
    *,
    quality: float = 7.5,
    collab: float = 6.0,
    windows: tuple[tuple[str, float, float], ...] = (("7d", 0.85, 7.5),),
) -> MagicMock:
    """Create a mock PerformanceTracker.

    Args:
        quality: Overall quality score.
        collab: Overall collaboration score.
        windows: Tuples of (window_size, success_rate, avg_quality).
    """
    tracker = MagicMock()
    snapshot = MagicMock()
    snapshot.overall_quality_score = quality
    snapshot.overall_collaboration_score = collab
    mock_windows = []
    for ws, sr, aq in windows:
        w = MagicMock()
        w.window_size = ws
        w.success_rate = sr
        w.avg_quality_score = aq
        mock_windows.append(w)
    snapshot.windows = tuple(mock_windows)
    snapshot.trends = ()
    tracker.get_snapshot = AsyncMock(return_value=snapshot)
    return tracker


# ── Performance aggregator ─────────────────────────────────────────


class TestPerformanceSignalAggregator:
    """Performance aggregator tests."""

    def test_domain_name(self) -> None:
        tracker = _make_mock_tracker()
        agg = PerformanceSignalAggregator(
            tracker=tracker,
            agent_ids_provider=list,
        )
        assert agg.domain == "performance"

    async def test_empty_org(self) -> None:
        tracker = _make_mock_tracker()
        agg = PerformanceSignalAggregator(
            tracker=tracker,
            agent_ids_provider=list,
        )
        result = await agg.aggregate(since=_week_ago(), until=_now())
        assert isinstance(result, OrgPerformanceSummary)
        assert result.agent_count == 0

    async def test_single_agent(self) -> None:
        tracker = _make_mock_tracker()
        agg = PerformanceSignalAggregator(
            tracker=tracker,
            agent_ids_provider=lambda: ["agent-1"],
        )
        result = await agg.aggregate(since=_week_ago(), until=_now())
        assert result.agent_count == 1
        assert result.avg_quality_score == 7.5
        assert result.avg_success_rate == 0.85
        assert result.avg_collaboration_score == 6.0

    async def test_multiple_agents_averaged(self) -> None:
        tracker = mock_of[PerformanceTracker]()
        s1 = MagicMock()
        s1.overall_quality_score = 8.0
        s1.overall_collaboration_score = 7.0
        w1 = MagicMock()
        w1.window_size = "7d"
        w1.success_rate = 0.90
        w1.avg_quality_score = 8.0
        s1.windows = (w1,)

        s2 = MagicMock()
        s2.overall_quality_score = 6.0
        s2.overall_collaboration_score = 5.0
        w2 = MagicMock()
        w2.window_size = "7d"
        w2.success_rate = 0.80
        w2.avg_quality_score = 6.0
        s2.windows = (w2,)

        tracker.get_snapshot = AsyncMock(side_effect=[s1, s2])

        agg = PerformanceSignalAggregator(
            tracker=tracker,
            agent_ids_provider=lambda: ["agent-1", "agent-2"],
        )
        result = await agg.aggregate(since=_week_ago(), until=_now())
        assert result.agent_count == 2
        assert result.avg_quality_score == 7.0
        assert result.avg_success_rate == 0.85

    async def test_multi_window_metrics(self) -> None:
        tracker = _make_mock_tracker(
            windows=(
                ("7d", 0.90, 8.0),
                ("30d", 0.85, 7.5),
                ("90d", 0.80, 7.0),
            ),
        )
        agg = PerformanceSignalAggregator(
            tracker=tracker,
            agent_ids_provider=lambda: ["agent-1"],
        )
        result = await agg.aggregate(since=_week_ago(), until=_now())
        metric_names = {m.name for m in result.metrics}
        assert "success_rate_7d" in metric_names
        assert "success_rate_30d" in metric_names
        assert "success_rate_90d" in metric_names
        assert "quality_7d" in metric_names
        assert "quality_30d" in metric_names
        assert "quality_90d" in metric_names
        # Check window_days are parsed correctly.
        for m in result.metrics:
            if m.name == "success_rate_30d":
                assert m.window_days == 30
                assert m.value == 0.85

    async def test_tracker_failure_returns_empty(self) -> None:
        tracker = mock_of[PerformanceTracker](
            get_snapshot=AsyncMock(side_effect=RuntimeError("tracker broken")),
        )
        agg = PerformanceSignalAggregator(
            tracker=tracker,
            agent_ids_provider=lambda: ["agent-1"],
        )
        result = await agg.aggregate(since=_week_ago(), until=_now())
        assert result.agent_count == 0

    @pytest.mark.parametrize("exc_cls", [MemoryError, RecursionError])
    async def test_propagates_catastrophic_interpreter_errors(
        self,
        exc_cls: type[BaseException],
    ) -> None:
        """Catastrophic interpreter errors escape the broad ``Exception`` net."""
        tracker = mock_of[PerformanceTracker](
            get_snapshot=AsyncMock(side_effect=exc_cls),
        )
        agg = PerformanceSignalAggregator(
            tracker=tracker,
            agent_ids_provider=lambda: ["agent-1"],
        )
        with pytest.raises(exc_cls):
            await agg.aggregate(since=_week_ago(), until=_now())


# ── Other aggregators ──────────────────────────────────────────────


class TestBudgetSignalAggregator:
    """Budget aggregator tests."""

    def test_domain_name(self) -> None:
        agg = BudgetSignalAggregator(cost_record_provider=list)
        assert agg.domain == "budget"

    async def test_returns_budget_summary(self) -> None:
        agg = BudgetSignalAggregator(cost_record_provider=list)
        result = await agg.aggregate(since=_week_ago(), until=_now())
        assert isinstance(result, OrgBudgetSummary)

    @pytest.mark.parametrize("exc_cls", [MemoryError, RecursionError])
    async def test_propagates_catastrophic_interpreter_errors(
        self,
        exc_cls: type[BaseException],
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Catastrophic interpreter errors escape the broad ``Exception`` net.

        The placeholder ``aggregate()`` body only calls ``logger.info``,
        so a real production trigger for the carve-out requires the
        logger itself to raise. Monkeypatching the module-level logger
        is the supported pattern; once the real implementation lands
        (bucket_cost_records / project_daily_spend / etc.) those calls
        become the natural injection point and this test will be
        rewritten against them.
        """
        from synthorg.meta.signals import budget as _budget_module

        def _raise(*_args: object, **_kwargs: object) -> None:
            raise exc_cls

        monkeypatch.setattr(_budget_module.logger, "info", _raise)
        agg = BudgetSignalAggregator(cost_record_provider=list)
        with pytest.raises(exc_cls):
            await agg.aggregate(since=_week_ago(), until=_now())


class TestCoordinationSignalAggregator:
    """Coordination aggregator tests."""

    async def test_returns_coordination_summary(self) -> None:
        agg = CoordinationSignalAggregator()
        result = await agg.aggregate(since=_week_ago(), until=_now())
        assert isinstance(result, OrgCoordinationSummary)


class TestScalingSignalAggregator:
    """Scaling aggregator tests."""

    async def test_returns_scaling_summary(self) -> None:
        service = mock_of[ScalingService](
            get_recent_decisions=MagicMock(return_value=()),
            get_recent_actions=MagicMock(return_value=()),
        )
        agg = ScalingSignalAggregator(service=service)
        result = await agg.aggregate(since=_week_ago(), until=_now())
        assert isinstance(result, OrgScalingSummary)


class TestErrorSignalAggregator:
    """Error aggregator tests."""

    async def test_returns_error_summary(self) -> None:
        agg = ErrorSignalAggregator()
        result = await agg.aggregate(since=_week_ago(), until=_now())
        assert isinstance(result, OrgErrorSummary)


class TestEvolutionSignalAggregator:
    """Evolution aggregator tests."""

    async def test_returns_evolution_summary(self) -> None:
        agg = EvolutionSignalAggregator()
        result = await agg.aggregate(since=_week_ago(), until=_now())
        assert isinstance(result, OrgEvolutionSummary)


class TestTelemetrySignalAggregator:
    """Telemetry aggregator tests."""

    async def test_returns_telemetry_summary(self) -> None:
        agg = TelemetrySignalAggregator()
        result = await agg.aggregate(since=_week_ago(), until=_now())
        assert isinstance(result, OrgTelemetrySummary)


# ── Snapshot builder ───────────────────────────────────────────────


class TestSnapshotBuilder:
    """SnapshotBuilder tests."""

    def _make_builder(self) -> SnapshotBuilder:
        """Create a builder with default aggregators."""
        tracker = _make_mock_tracker()
        scaling_service = mock_of[ScalingService](
            get_recent_decisions=MagicMock(return_value=()),
            get_recent_actions=MagicMock(return_value=()),
        )
        return SnapshotBuilder(
            performance=PerformanceSignalAggregator(
                tracker=tracker,
                agent_ids_provider=lambda: ["agent-1"],
            ),
            budget=BudgetSignalAggregator(cost_record_provider=list),
            coordination=CoordinationSignalAggregator(),
            scaling=ScalingSignalAggregator(service=scaling_service),
            errors=ErrorSignalAggregator(),
            evolution=EvolutionSignalAggregator(),
            telemetry=TelemetrySignalAggregator(),
        )

    async def test_build_returns_snapshot(self) -> None:
        builder = self._make_builder()
        snapshot = await builder.build(since=_week_ago())
        assert isinstance(snapshot, OrgSignalSnapshot)
        assert isinstance(snapshot.performance, OrgPerformanceSummary)
        assert isinstance(snapshot.budget, OrgBudgetSummary)
        assert isinstance(snapshot.coordination, OrgCoordinationSummary)
        assert isinstance(snapshot.scaling, OrgScalingSummary)
        assert isinstance(snapshot.errors, OrgErrorSummary)
        assert isinstance(snapshot.evolution, OrgEvolutionSummary)
        assert isinstance(snapshot.telemetry, OrgTelemetrySummary)

    async def test_build_with_explicit_until(self) -> None:
        builder = self._make_builder()
        snapshot = await builder.build(since=_week_ago(), until=_now())
        assert snapshot.collected_at is not None

    async def test_build_returns_snapshot_with_performance(self) -> None:
        """Build returns a snapshot with aggregated performance data."""
        builder = self._make_builder()
        snapshot = await builder.build(since=_week_ago())
        # Performance aggregator should have been called.
        assert snapshot.performance.agent_count == 1
