"""Tests for PerformanceTracker service."""

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import TrendDirection
from synthorg.hr.performance.models import (
    CollaborationMetricRecord,
    CollaborationScoreResult,
    QualityScoreResult,
    TaskMetricRecord,
    TrendResult,
    WindowMetrics,
)
from synthorg.hr.performance.tracker import PerformanceTracker

if TYPE_CHECKING:
    from synthorg.hr.performance.config import PerformanceConfig

from .conftest import make_acceptance_criterion, make_collab_metric, make_task_metric

NOW = datetime(2026, 3, 10, 12, 0, 0, tzinfo=UTC)


# ── Mock Strategies ───────────────────────────────────────────────


class MockQualityStrategy:
    """Mock quality scoring strategy for tracker tests."""

    @property
    def name(self) -> str:
        return "mock_quality"

    async def score(
        self,
        *,
        agent_id: NotBlankStr,
        task_id: NotBlankStr,
        task_result: TaskMetricRecord,
        acceptance_criteria: tuple[object, ...],
    ) -> QualityScoreResult:
        return QualityScoreResult(
            score=8.0,
            strategy_name=NotBlankStr("mock_quality"),
            breakdown=(("mock", 8.0),),
            confidence=0.9,
        )


class MockCollaborationStrategy:
    """Mock collaboration scoring strategy for tracker tests."""

    def __init__(self, *, score: float = 7.0, confidence: float = 0.8) -> None:
        self._score = score
        self._confidence = confidence

    @property
    def name(self) -> str:
        return "mock_collab"

    async def score(
        self,
        *,
        agent_id: NotBlankStr,
        records: tuple[CollaborationMetricRecord, ...],
        role_weights: dict[str, float] | None = None,
    ) -> CollaborationScoreResult:
        return CollaborationScoreResult(
            score=self._score,
            strategy_name=NotBlankStr("mock_collab"),
            component_scores=(),
            confidence=self._confidence,
        )


class MockWindowStrategy:
    """Mock window strategy for tracker tests."""

    def __init__(
        self,
        *,
        min_data_points: int = 5,
        windows: tuple[WindowMetrics, ...] | None = None,
    ) -> None:
        self._min_data_points = min_data_points
        self._windows = windows or ()

    @property
    def name(self) -> str:
        return "mock_window"

    @property
    def min_data_points(self) -> int:
        return self._min_data_points

    def compute_windows(
        self,
        records: tuple[TaskMetricRecord, ...],
        *,
        now: datetime,
    ) -> tuple[WindowMetrics, ...]:
        return self._windows


class MockTrendStrategy:
    """Mock trend strategy for tracker tests."""

    def __init__(
        self,
        *,
        direction: TrendDirection = TrendDirection.STABLE,
    ) -> None:
        self._direction = direction

    @property
    def name(self) -> str:
        return "mock_trend"

    def detect(
        self,
        *,
        metric_name: NotBlankStr,
        values: tuple[tuple[datetime, float], ...],
        window_size: NotBlankStr,
    ) -> TrendResult:
        return TrendResult(
            metric_name=metric_name,
            window_size=window_size,
            direction=self._direction,
            slope=0.0,
            data_point_count=len(values),
        )


# ── Helper ────────────────────────────────────────────────────────


def _make_tracker(
    *,
    quality: MockQualityStrategy | None = None,
    collaboration: MockCollaborationStrategy | None = None,
    window: MockWindowStrategy | None = None,
    trend: MockTrendStrategy | None = None,
    config: PerformanceConfig | None = None,
) -> PerformanceTracker:
    return PerformanceTracker(
        quality_strategy=quality or MockQualityStrategy(),
        collaboration_strategy=collaboration or MockCollaborationStrategy(),
        window_strategy=window or MockWindowStrategy(),
        trend_strategy=trend or MockTrendStrategy(),
        config=config,
    )


# ── Tests ─────────────────────────────────────────────────────────


@pytest.mark.unit
class TestRecordTaskMetric:
    """PerformanceTracker.record_task_metric."""

    async def test_stores_and_returns_record(self) -> None:
        tracker = _make_tracker()
        record = make_task_metric(completed_at=NOW)

        result = await tracker.record_task_metric(record)

        assert result is record
        metrics = tracker.get_task_metrics(
            agent_id=NotBlankStr("agent-001"),
        )
        assert len(metrics) == 1
        assert metrics[0] is record

    async def test_multiple_records_same_agent(self) -> None:
        tracker = _make_tracker()
        r1 = make_task_metric(task_id="task-001", completed_at=NOW)
        r2 = make_task_metric(task_id="task-002", completed_at=NOW)

        await tracker.record_task_metric(r1)
        await tracker.record_task_metric(r2)

        metrics = tracker.get_task_metrics(
            agent_id=NotBlankStr("agent-001"),
        )
        assert len(metrics) == 2


@pytest.mark.unit
class TestRecordCollaborationEvent:
    """PerformanceTracker.record_collaboration_event."""

    async def test_stores_record(self) -> None:
        tracker = _make_tracker()
        record = make_collab_metric(recorded_at=NOW)

        await tracker.record_collaboration_event(record)

        metrics = tracker.get_collaboration_metrics(
            agent_id=NotBlankStr("agent-001"),
        )
        assert len(metrics) == 1
        assert metrics[0] is record


@pytest.mark.unit
class TestScoreTaskQuality:
    """PerformanceTracker.score_task_quality."""

    async def test_returns_updated_record(self) -> None:
        tracker = _make_tracker()
        record = make_task_metric(completed_at=NOW)

        result = await tracker.score_task_quality(
            agent_id=NotBlankStr("agent-001"),
            task_id=NotBlankStr("task-001"),
            task_result=record,
            acceptance_criteria=(make_acceptance_criterion(met=True),),
        )

        # Mock strategy always returns 8.0
        assert result.quality_score == 8.0
        # Original record unchanged (frozen)
        assert record.quality_score is None


@pytest.mark.unit
class TestGetSnapshot:
    """PerformanceTracker.get_snapshot."""

    async def test_empty_state_returns_snapshot(self) -> None:
        tracker = _make_tracker(
            collaboration=MockCollaborationStrategy(confidence=0.0),
        )

        snapshot = await tracker.get_snapshot(
            NotBlankStr("agent-001"),
            now=NOW,
        )

        assert snapshot.agent_id == "agent-001"
        assert snapshot.computed_at == NOW
        assert snapshot.windows == ()
        assert snapshot.trends == ()
        assert snapshot.overall_quality_score is None
        # Confidence is 0 -> overall_collaboration_score is None
        assert snapshot.overall_collaboration_score is None

    async def test_snapshot_with_windows_and_trends(self) -> None:
        """Snapshot includes windows and trends from strategies."""
        window = WindowMetrics(
            window_size=NotBlankStr("7d"),
            data_point_count=10,
            tasks_completed=8,
            tasks_failed=2,
            success_rate=0.8,
        )
        tracker = _make_tracker(
            window=MockWindowStrategy(
                windows=(window,),
                min_data_points=5,
            ),
        )
        # Add a scored task record so trend computation has data
        record = make_task_metric(
            completed_at=NOW - timedelta(hours=1),
            quality_score=7.5,
        )
        await tracker.record_task_metric(record)

        snapshot = await tracker.get_snapshot(
            NotBlankStr("agent-001"),
            now=NOW,
        )

        assert len(snapshot.windows) == 1
        assert snapshot.windows[0].window_size == "7d"
        # Trends should be computed (quality_score + cost_usd)
        assert len(snapshot.trends) == 2
        assert snapshot.overall_quality_score == 7.5

    async def test_snapshot_collaboration_score_included(self) -> None:
        """Snapshot includes collaboration score when confidence > 0."""
        tracker = _make_tracker(
            collaboration=MockCollaborationStrategy(
                score=6.5,
                confidence=0.5,
            ),
        )

        snapshot = await tracker.get_snapshot(
            NotBlankStr("agent-001"),
            now=NOW,
        )

        assert snapshot.overall_collaboration_score == 6.5

    async def test_snapshot_uses_current_time_when_now_none(self) -> None:
        tracker = _make_tracker()

        snapshot = await tracker.get_snapshot(NotBlankStr("agent-001"))

        # Should be close to current time
        assert snapshot.computed_at is not None


@pytest.mark.unit
class TestGetTaskMetrics:
    """PerformanceTracker.get_task_metrics filtering."""

    async def test_filter_by_agent_id(self) -> None:
        tracker = _make_tracker()
        r1 = make_task_metric(
            agent_id="agent-001",
            completed_at=NOW,
        )
        r2 = make_task_metric(
            agent_id="agent-002",
            completed_at=NOW,
        )
        await tracker.record_task_metric(r1)
        await tracker.record_task_metric(r2)

        result = tracker.get_task_metrics(
            agent_id=NotBlankStr("agent-001"),
        )

        assert len(result) == 1
        assert result[0].agent_id == "agent-001"

    async def test_filter_by_since(self) -> None:
        tracker = _make_tracker()
        old = make_task_metric(
            completed_at=NOW - timedelta(days=10),
        )
        recent = make_task_metric(
            task_id="task-002",
            completed_at=NOW - timedelta(hours=1),
        )
        await tracker.record_task_metric(old)
        await tracker.record_task_metric(recent)

        result = tracker.get_task_metrics(
            since=NOW - timedelta(days=1),
        )

        assert len(result) == 1
        assert result[0].task_id == "task-002"

    async def test_filter_by_until(self) -> None:
        tracker = _make_tracker()
        early = make_task_metric(
            task_id="task-early",
            completed_at=NOW - timedelta(days=5),
        )
        late = make_task_metric(
            task_id="task-late",
            completed_at=NOW,
        )
        await tracker.record_task_metric(early)
        await tracker.record_task_metric(late)

        result = tracker.get_task_metrics(
            until=NOW - timedelta(days=1),
        )

        assert len(result) == 1
        assert result[0].task_id == "task-early"

    async def test_no_filters_returns_all(self) -> None:
        tracker = _make_tracker()
        r1 = make_task_metric(
            agent_id="agent-001",
            completed_at=NOW,
        )
        r2 = make_task_metric(
            agent_id="agent-002",
            completed_at=NOW,
        )
        await tracker.record_task_metric(r1)
        await tracker.record_task_metric(r2)

        result = tracker.get_task_metrics()

        assert len(result) == 2


@pytest.mark.unit
class TestGetCollaborationMetrics:
    """PerformanceTracker.get_collaboration_metrics filtering."""

    async def test_filter_by_agent_id(self) -> None:
        tracker = _make_tracker()
        r1 = make_collab_metric(
            agent_id="agent-001",
            recorded_at=NOW,
        )
        r2 = make_collab_metric(
            agent_id="agent-002",
            recorded_at=NOW,
        )
        await tracker.record_collaboration_event(r1)
        await tracker.record_collaboration_event(r2)

        result = tracker.get_collaboration_metrics(
            agent_id=NotBlankStr("agent-001"),
        )

        assert len(result) == 1
        assert result[0].agent_id == "agent-001"

    async def test_filter_by_since(self) -> None:
        tracker = _make_tracker()
        old = make_collab_metric(
            recorded_at=NOW - timedelta(days=10),
        )
        recent = make_collab_metric(
            recorded_at=NOW - timedelta(hours=1),
        )
        await tracker.record_collaboration_event(old)
        await tracker.record_collaboration_event(recent)

        result = tracker.get_collaboration_metrics(
            since=NOW - timedelta(days=1),
        )

        assert len(result) == 1

    async def test_no_filters_returns_all(self) -> None:
        tracker = _make_tracker()
        r1 = make_collab_metric(
            agent_id="agent-001",
            recorded_at=NOW,
        )
        r2 = make_collab_metric(
            agent_id="agent-002",
            recorded_at=NOW,
        )
        await tracker.record_collaboration_event(r1)
        await tracker.record_collaboration_event(r2)

        result = tracker.get_collaboration_metrics()

        assert len(result) == 2


@pytest.mark.unit
class TestMultipleAgents:
    """PerformanceTracker with multiple agents."""

    async def test_records_isolated_per_agent(self) -> None:
        tracker = _make_tracker()
        r1 = make_task_metric(
            agent_id="agent-001",
            completed_at=NOW,
        )
        r2 = make_task_metric(
            agent_id="agent-002",
            completed_at=NOW,
        )
        await tracker.record_task_metric(r1)
        await tracker.record_task_metric(r2)

        m1 = tracker.get_task_metrics(
            agent_id=NotBlankStr("agent-001"),
        )
        m2 = tracker.get_task_metrics(
            agent_id=NotBlankStr("agent-002"),
        )

        assert len(m1) == 1
        assert len(m2) == 1
        assert m1[0].agent_id == "agent-001"
        assert m2[0].agent_id == "agent-002"

    async def test_snapshot_per_agent(self) -> None:
        """Each agent gets independent snapshot."""
        tracker = _make_tracker(
            collaboration=MockCollaborationStrategy(confidence=0.0),
        )
        r1 = make_task_metric(
            agent_id="agent-001",
            completed_at=NOW,
            quality_score=9.0,
        )
        r2 = make_task_metric(
            agent_id="agent-002",
            completed_at=NOW,
            quality_score=5.0,
        )
        await tracker.record_task_metric(r1)
        await tracker.record_task_metric(r2)

        snap1 = await tracker.get_snapshot(
            NotBlankStr("agent-001"),
            now=NOW,
        )
        snap2 = await tracker.get_snapshot(
            NotBlankStr("agent-002"),
            now=NOW,
        )

        assert snap1.overall_quality_score == 9.0
        assert snap2.overall_quality_score == 5.0

    async def test_unknown_agent_empty(self) -> None:
        tracker = _make_tracker(
            collaboration=MockCollaborationStrategy(confidence=0.0),
        )

        snapshot = await tracker.get_snapshot(
            NotBlankStr("unknown"),
            now=NOW,
        )

        assert snapshot.overall_quality_score is None
        assert snapshot.windows == ()
        assert snapshot.trends == ()
