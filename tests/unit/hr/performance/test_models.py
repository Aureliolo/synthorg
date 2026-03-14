"""Tests for performance tracking domain models."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.core.enums import Complexity, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import TrendDirection
from synthorg.hr.performance.models import (
    AgentPerformanceSnapshot,
    CollaborationMetricRecord,
    CollaborationScoreResult,
    QualityScoreResult,
    TrendResult,
    WindowMetrics,
)

from .conftest import make_collab_metric, make_task_metric

NOW = datetime(2026, 3, 10, 12, 0, 0, tzinfo=UTC)


# ── TaskMetricRecord ──────────────────────────────────────────────


@pytest.mark.unit
class TestTaskMetricRecord:
    """TaskMetricRecord construction, frozen enforcement, validation."""

    def test_valid_construction(self) -> None:
        record = make_task_metric(completed_at=NOW)
        assert record.agent_id == "agent-001"
        assert record.task_id == "task-001"
        assert record.task_type == TaskType.DEVELOPMENT
        assert record.completed_at == NOW
        assert record.is_success is True
        assert record.duration_seconds == 60.0
        assert record.cost_usd == 0.5
        assert record.turns_used == 5
        assert record.tokens_used == 1000
        assert record.quality_score is None
        assert record.complexity == Complexity.MEDIUM

    def test_frozen_enforcement(self) -> None:
        record = make_task_metric()
        with pytest.raises(ValidationError):
            record.agent_id = "other"  # type: ignore[misc]

    def test_id_auto_generated(self) -> None:
        r1 = make_task_metric()
        r2 = make_task_metric()
        assert r1.id != r2.id
        assert len(r1.id) > 0

    def test_quality_score_none_allowed(self) -> None:
        record = make_task_metric(quality_score=None)
        assert record.quality_score is None

    def test_quality_score_valid(self) -> None:
        record = make_task_metric(quality_score=7.5)
        assert record.quality_score == 7.5

    @pytest.mark.parametrize(
        "quality_score",
        [0.0, 10.0],
        ids=["min_boundary", "max_boundary"],
    )
    def test_quality_score_boundaries(self, quality_score: float) -> None:
        record = make_task_metric(quality_score=quality_score)
        assert record.quality_score == quality_score

    @pytest.mark.parametrize(
        "quality_score",
        [-0.1, 10.1],
        ids=["below_min", "above_max"],
    )
    def test_quality_score_out_of_range(self, quality_score: float) -> None:
        with pytest.raises(ValidationError):
            make_task_metric(quality_score=quality_score)

    def test_negative_duration_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_task_metric(duration_seconds=-1.0)

    def test_negative_cost_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_task_metric(cost_usd=-0.01)

    def test_negative_turns_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_task_metric(turns_used=-1)

    def test_negative_tokens_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_task_metric(tokens_used=-1)

    def test_zero_values_allowed(self) -> None:
        record = make_task_metric(
            duration_seconds=0.0,
            cost_usd=0.0,
            turns_used=0,
            tokens_used=0,
        )
        assert record.duration_seconds == 0.0
        assert record.cost_usd == 0.0
        assert record.turns_used == 0
        assert record.tokens_used == 0

    def test_blank_agent_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_task_metric(agent_id="   ")

    def test_inf_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_task_metric(cost_usd=float("inf"))


# ── CollaborationMetricRecord ─────────────────────────────────────


@pytest.mark.unit
class TestCollaborationMetricRecord:
    """CollaborationMetricRecord construction, None components, ranges."""

    def test_valid_construction(self) -> None:
        record = make_collab_metric(
            recorded_at=NOW,
            delegation_success=True,
            delegation_response_seconds=10.0,
            conflict_constructiveness=0.8,
            meeting_contribution=0.9,
            handoff_completeness=0.7,
        )
        assert record.agent_id == "agent-001"
        assert record.recorded_at == NOW
        assert record.delegation_success is True
        assert record.delegation_response_seconds == 10.0
        assert record.conflict_constructiveness == 0.8
        assert record.meeting_contribution == 0.9
        assert record.loop_triggered is False
        assert record.handoff_completeness == 0.7

    def test_frozen_enforcement(self) -> None:
        record = make_collab_metric()
        with pytest.raises(ValidationError):
            record.agent_id = "other"  # type: ignore[misc]

    def test_none_components(self) -> None:
        record = make_collab_metric()
        assert record.delegation_success is None
        assert record.delegation_response_seconds is None
        assert record.conflict_constructiveness is None
        assert record.meeting_contribution is None
        assert record.handoff_completeness is None

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("conflict_constructiveness", -0.1),
            ("conflict_constructiveness", 1.1),
            ("meeting_contribution", -0.1),
            ("meeting_contribution", 1.1),
            ("handoff_completeness", -0.1),
            ("handoff_completeness", 1.1),
            ("delegation_response_seconds", -1.0),
        ],
        ids=[
            "conflict_below_0",
            "conflict_above_1",
            "meeting_below_0",
            "meeting_above_1",
            "handoff_below_0",
            "handoff_above_1",
            "response_negative",
        ],
    )
    def test_range_validation(self, field: str, value: float) -> None:
        with pytest.raises(ValidationError):
            CollaborationMetricRecord(
                agent_id=NotBlankStr("agent-001"),
                recorded_at=NOW,
                **{field: value},  # type: ignore[arg-type]
            )

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("conflict_constructiveness", 0.0),
            ("conflict_constructiveness", 1.0),
            ("meeting_contribution", 0.0),
            ("meeting_contribution", 1.0),
            ("handoff_completeness", 0.0),
            ("handoff_completeness", 1.0),
        ],
        ids=[
            "conflict_0",
            "conflict_1",
            "meeting_0",
            "meeting_1",
            "handoff_0",
            "handoff_1",
        ],
    )
    def test_boundary_values_accepted(
        self,
        field: str,
        value: float,
    ) -> None:
        record = CollaborationMetricRecord(
            agent_id=NotBlankStr("agent-001"),
            recorded_at=NOW,
            **{field: value},  # type: ignore[arg-type]
        )
        assert getattr(record, field) == value


# ── QualityScoreResult ────────────────────────────────────────────


@pytest.mark.unit
class TestQualityScoreResult:
    """QualityScoreResult construction, score range, breakdown."""

    def test_valid_construction(self) -> None:
        result = QualityScoreResult(
            score=7.5,
            strategy_name=NotBlankStr("ci_signal"),
            breakdown=(("criteria", 8.0), ("success", 10.0)),
            confidence=0.9,
        )
        assert result.score == 7.5
        assert result.strategy_name == "ci_signal"
        assert len(result.breakdown) == 2
        assert result.confidence == 0.9

    @pytest.mark.parametrize(
        "score",
        [0.0, 10.0],
        ids=["min", "max"],
    )
    def test_score_boundaries(self, score: float) -> None:
        result = QualityScoreResult(
            score=score,
            strategy_name=NotBlankStr("test"),
            confidence=0.5,
        )
        assert result.score == score

    @pytest.mark.parametrize(
        "score",
        [-0.1, 10.1],
        ids=["below_min", "above_max"],
    )
    def test_score_out_of_range(self, score: float) -> None:
        with pytest.raises(ValidationError):
            QualityScoreResult(
                score=score,
                strategy_name=NotBlankStr("test"),
                confidence=0.5,
            )

    @pytest.mark.parametrize(
        "confidence",
        [-0.1, 1.1],
        ids=["below_0", "above_1"],
    )
    def test_confidence_out_of_range(self, confidence: float) -> None:
        with pytest.raises(ValidationError):
            QualityScoreResult(
                score=5.0,
                strategy_name=NotBlankStr("test"),
                confidence=confidence,
            )

    def test_empty_breakdown(self) -> None:
        result = QualityScoreResult(
            score=5.0,
            strategy_name=NotBlankStr("test"),
            confidence=0.5,
        )
        assert result.breakdown == ()

    def test_frozen(self) -> None:
        result = QualityScoreResult(
            score=5.0,
            strategy_name=NotBlankStr("test"),
            confidence=0.5,
        )
        with pytest.raises(ValidationError):
            result.score = 9.0  # type: ignore[misc]


# ── CollaborationScoreResult ──────────────────────────────────────


@pytest.mark.unit
class TestCollaborationScoreResult:
    """CollaborationScoreResult construction and score range."""

    def test_valid_construction(self) -> None:
        result = CollaborationScoreResult(
            score=6.0,
            strategy_name=NotBlankStr("behavioral"),
            component_scores=(("delegation", 7.0), ("handoff", 5.0)),
            confidence=0.8,
        )
        assert result.score == 6.0
        assert len(result.component_scores) == 2

    @pytest.mark.parametrize(
        "score",
        [0.0, 10.0],
        ids=["min", "max"],
    )
    def test_score_boundaries(self, score: float) -> None:
        result = CollaborationScoreResult(
            score=score,
            strategy_name=NotBlankStr("test"),
            confidence=0.5,
        )
        assert result.score == score

    @pytest.mark.parametrize(
        "score",
        [-0.1, 10.1],
        ids=["below_min", "above_max"],
    )
    def test_score_out_of_range(self, score: float) -> None:
        with pytest.raises(ValidationError):
            CollaborationScoreResult(
                score=score,
                strategy_name=NotBlankStr("test"),
                confidence=0.5,
            )


# ── TrendResult ───────────────────────────────────────────────────


@pytest.mark.unit
class TestTrendResult:
    """TrendResult construction and direction enum."""

    def test_valid_construction(self) -> None:
        result = TrendResult(
            metric_name=NotBlankStr("quality_score"),
            window_size=NotBlankStr("7d"),
            direction=TrendDirection.IMPROVING,
            slope=0.1,
            data_point_count=10,
        )
        assert result.direction == TrendDirection.IMPROVING
        assert result.slope == 0.1
        assert result.data_point_count == 10

    @pytest.mark.parametrize(
        "direction",
        list(TrendDirection),
        ids=[d.value for d in TrendDirection],
    )
    def test_all_directions(self, direction: TrendDirection) -> None:
        result = TrendResult(
            metric_name=NotBlankStr("metric"),
            window_size=NotBlankStr("30d"),
            direction=direction,
            slope=0.0,
            data_point_count=5,
        )
        assert result.direction == direction

    def test_negative_data_point_count_rejected(self) -> None:
        with pytest.raises(ValidationError):
            TrendResult(
                metric_name=NotBlankStr("metric"),
                window_size=NotBlankStr("7d"),
                direction=TrendDirection.STABLE,
                slope=0.0,
                data_point_count=-1,
            )


# ── WindowMetrics ─────────────────────────────────────────────────


@pytest.mark.unit
class TestWindowMetrics:
    """WindowMetrics construction, None aggregates, success_rate."""

    def test_valid_construction(self) -> None:
        wm = WindowMetrics(
            window_size=NotBlankStr("7d"),
            data_point_count=10,
            tasks_completed=8,
            tasks_failed=2,
            avg_quality_score=7.5,
            avg_cost_per_task=0.5,
            avg_completion_time_seconds=120.0,
            avg_tokens_per_task=2000.0,
            success_rate=0.8,
            collaboration_score=6.0,
        )
        assert wm.window_size == "7d"
        assert wm.data_point_count == 10
        assert wm.success_rate == 0.8

    def test_none_aggregate_values(self) -> None:
        wm = WindowMetrics(
            window_size=NotBlankStr("7d"),
            data_point_count=0,
            tasks_completed=0,
            tasks_failed=0,
        )
        assert wm.avg_quality_score is None
        assert wm.avg_cost_per_task is None
        assert wm.avg_completion_time_seconds is None
        assert wm.avg_tokens_per_task is None
        assert wm.success_rate is None
        assert wm.collaboration_score is None

    @pytest.mark.parametrize(
        "rate",
        [0.0, 1.0],
        ids=["zero", "one"],
    )
    def test_success_rate_boundaries(self, rate: float) -> None:
        wm = WindowMetrics(
            window_size=NotBlankStr("7d"),
            data_point_count=5,
            tasks_completed=5,
            tasks_failed=0,
            success_rate=rate,
        )
        assert wm.success_rate == rate

    @pytest.mark.parametrize(
        "rate",
        [-0.1, 1.1],
        ids=["below_0", "above_1"],
    )
    def test_success_rate_out_of_range(self, rate: float) -> None:
        with pytest.raises(ValidationError):
            WindowMetrics(
                window_size=NotBlankStr("7d"),
                data_point_count=5,
                tasks_completed=5,
                tasks_failed=0,
                success_rate=rate,
            )

    def test_frozen(self) -> None:
        wm = WindowMetrics(
            window_size=NotBlankStr("7d"),
            data_point_count=0,
            tasks_completed=0,
            tasks_failed=0,
        )
        with pytest.raises(ValidationError):
            wm.data_point_count = 5  # type: ignore[misc]


# ── AgentPerformanceSnapshot ──────────────────────────────────────


@pytest.mark.unit
class TestAgentPerformanceSnapshot:
    """AgentPerformanceSnapshot construction, empty windows/trends."""

    def test_valid_construction(self) -> None:
        snap = AgentPerformanceSnapshot(
            agent_id=NotBlankStr("agent-001"),
            computed_at=NOW,
            overall_quality_score=8.0,
            overall_collaboration_score=7.0,
        )
        assert snap.agent_id == "agent-001"
        assert snap.computed_at == NOW
        assert snap.windows == ()
        assert snap.trends == ()
        assert snap.overall_quality_score == 8.0
        assert snap.overall_collaboration_score == 7.0

    def test_empty_windows_and_trends(self) -> None:
        snap = AgentPerformanceSnapshot(
            agent_id=NotBlankStr("agent-001"),
            computed_at=NOW,
        )
        assert snap.windows == ()
        assert snap.trends == ()
        assert snap.overall_quality_score is None
        assert snap.overall_collaboration_score is None

    @pytest.mark.parametrize(
        "score",
        [0.0, 10.0],
        ids=["min", "max"],
    )
    def test_quality_score_boundaries(self, score: float) -> None:
        snap = AgentPerformanceSnapshot(
            agent_id=NotBlankStr("agent-001"),
            computed_at=NOW,
            overall_quality_score=score,
        )
        assert snap.overall_quality_score == score

    @pytest.mark.parametrize(
        "score",
        [-0.1, 10.1],
        ids=["below_min", "above_max"],
    )
    def test_quality_score_out_of_range(self, score: float) -> None:
        with pytest.raises(ValidationError):
            AgentPerformanceSnapshot(
                agent_id=NotBlankStr("agent-001"),
                computed_at=NOW,
                overall_quality_score=score,
            )

    def test_frozen(self) -> None:
        snap = AgentPerformanceSnapshot(
            agent_id=NotBlankStr("agent-001"),
            computed_at=NOW,
        )
        with pytest.raises(ValidationError):
            snap.agent_id = "other"  # type: ignore[misc]
