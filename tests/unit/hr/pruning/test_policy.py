"""Tests for pruning policy implementations."""

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import TrendDirection
from synthorg.hr.pruning.policy import (
    ThresholdPruningPolicy,
    ThresholdPruningPolicyConfig,
    TrendPruningPolicy,
    TrendPruningPolicyConfig,
)

from .conftest import (
    make_performance_snapshot,
    make_trend_result,
    make_window_metrics,
)

# ── ThresholdPruningPolicy ───────────────────────────────────────


@pytest.mark.unit
class TestThresholdPruningPolicyConfig:
    """ThresholdPruningPolicyConfig validation."""

    def test_defaults(self) -> None:
        config = ThresholdPruningPolicyConfig()
        assert config.quality_threshold == 3.5
        assert config.minimum_consecutive_windows == 2
        assert config.minimum_window_data_points == 5

    @pytest.mark.parametrize(
        "threshold",
        [0.0, 5.0, 10.0],
        ids=["min", "mid", "max"],
    )
    def test_quality_threshold_boundaries(self, threshold: float) -> None:
        config = ThresholdPruningPolicyConfig(quality_threshold=threshold)
        assert config.quality_threshold == threshold

    @pytest.mark.parametrize(
        "threshold",
        [-0.1, 10.1],
        ids=["below_min", "above_max"],
    )
    def test_quality_threshold_out_of_range(self, threshold: float) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            ThresholdPruningPolicyConfig(quality_threshold=threshold)


@pytest.mark.unit
class TestThresholdPruningPolicy:
    """ThresholdPruningPolicy evaluation logic."""

    async def test_ineligible_empty_windows(self) -> None:
        policy = ThresholdPruningPolicy(ThresholdPruningPolicyConfig())
        snapshot = make_performance_snapshot(windows=())
        result = await policy.evaluate(NotBlankStr("agent-001"), snapshot)
        assert result.eligible is False

    async def test_ineligible_scores_above_threshold(self) -> None:
        policy = ThresholdPruningPolicy(ThresholdPruningPolicyConfig())
        windows = (
            make_window_metrics(
                window_size="7d",
                avg_quality_score=7.0,
            ),
            make_window_metrics(
                window_size="30d",
                avg_quality_score=6.5,
            ),
        )
        snapshot = make_performance_snapshot(windows=windows)
        result = await policy.evaluate(NotBlankStr("agent-001"), snapshot)
        assert result.eligible is False

    async def test_ineligible_only_one_window_below(self) -> None:
        """One window below threshold but minimum_consecutive_windows=2."""
        policy = ThresholdPruningPolicy(
            ThresholdPruningPolicyConfig(minimum_consecutive_windows=2),
        )
        windows = (
            make_window_metrics(
                window_size="7d",
                avg_quality_score=2.0,
            ),
            make_window_metrics(
                window_size="30d",
                avg_quality_score=7.0,
            ),
        )
        snapshot = make_performance_snapshot(windows=windows)
        result = await policy.evaluate(NotBlankStr("agent-001"), snapshot)
        assert result.eligible is False

    async def test_eligible_two_consecutive_windows_below(self) -> None:
        policy = ThresholdPruningPolicy(
            ThresholdPruningPolicyConfig(minimum_consecutive_windows=2),
        )
        windows = (
            make_window_metrics(
                window_size="7d",
                avg_quality_score=2.0,
            ),
            make_window_metrics(
                window_size="30d",
                avg_quality_score=3.0,
            ),
        )
        snapshot = make_performance_snapshot(windows=windows)
        result = await policy.evaluate(NotBlankStr("agent-001"), snapshot)
        assert result.eligible is True
        assert result.policy_name == "threshold"
        assert len(result.reasons) >= 1

    async def test_eligible_all_three_windows_below(self) -> None:
        policy = ThresholdPruningPolicy(
            ThresholdPruningPolicyConfig(minimum_consecutive_windows=2),
        )
        windows = (
            make_window_metrics(
                window_size="7d",
                avg_quality_score=1.0,
            ),
            make_window_metrics(
                window_size="30d",
                avg_quality_score=2.0,
            ),
            make_window_metrics(
                window_size="90d",
                avg_quality_score=3.0,
            ),
        )
        snapshot = make_performance_snapshot(windows=windows)
        result = await policy.evaluate(NotBlankStr("agent-001"), snapshot)
        assert result.eligible is True

    async def test_exactly_at_threshold_not_eligible(self) -> None:
        """Scores exactly at threshold are NOT below -- agent is safe."""
        policy = ThresholdPruningPolicy(
            ThresholdPruningPolicyConfig(
                quality_threshold=3.5,
                minimum_consecutive_windows=2,
            ),
        )
        windows = (
            make_window_metrics(
                window_size="7d",
                avg_quality_score=3.5,
            ),
            make_window_metrics(
                window_size="30d",
                avg_quality_score=3.5,
            ),
        )
        snapshot = make_performance_snapshot(windows=windows)
        result = await policy.evaluate(NotBlankStr("agent-001"), snapshot)
        assert result.eligible is False

    async def test_none_scores_break_consecutive_streak(self) -> None:
        """Windows with None scores skip and break the streak."""
        policy = ThresholdPruningPolicy(
            ThresholdPruningPolicyConfig(minimum_consecutive_windows=2),
        )
        windows = (
            make_window_metrics(
                window_size="7d",
                avg_quality_score=2.0,
            ),
            make_window_metrics(
                window_size="30d",
                avg_quality_score=None,
                data_point_count=0,
                tasks_completed=0,
                tasks_failed=0,
            ),
            make_window_metrics(
                window_size="90d",
                avg_quality_score=2.0,
            ),
        )
        snapshot = make_performance_snapshot(windows=windows)
        result = await policy.evaluate(NotBlankStr("agent-001"), snapshot)
        assert result.eligible is False

    async def test_insufficient_data_points_skip_window(self) -> None:
        """Windows below minimum_window_data_points are skipped."""
        policy = ThresholdPruningPolicy(
            ThresholdPruningPolicyConfig(
                minimum_consecutive_windows=2,
                minimum_window_data_points=5,
            ),
        )
        windows = (
            make_window_metrics(
                window_size="7d",
                avg_quality_score=2.0,
                data_point_count=3,
                tasks_completed=2,
                tasks_failed=1,
            ),
            make_window_metrics(
                window_size="30d",
                avg_quality_score=2.0,
            ),
        )
        snapshot = make_performance_snapshot(windows=windows)
        result = await policy.evaluate(NotBlankStr("agent-001"), snapshot)
        assert result.eligible is False

    async def test_scores_dict_populated(self) -> None:
        policy = ThresholdPruningPolicy(ThresholdPruningPolicyConfig())
        windows = (
            make_window_metrics(
                window_size="7d",
                avg_quality_score=2.0,
            ),
            make_window_metrics(
                window_size="30d",
                avg_quality_score=3.0,
            ),
        )
        snapshot = make_performance_snapshot(windows=windows)
        result = await policy.evaluate(NotBlankStr("agent-001"), snapshot)
        assert "quality" in result.scores or "overall_quality" in result.scores

    async def test_unmeasured_windows_never_qualify(self) -> None:
        """A window whose quality is unmeasured cannot be evidence of failure."""
        policy = ThresholdPruningPolicy(
            ThresholdPruningPolicyConfig(minimum_consecutive_windows=2),
        )
        windows = (
            make_window_metrics(window_size="7d", avg_quality_score=None),
            make_window_metrics(window_size="30d", avg_quality_score=None),
        )
        snapshot = make_performance_snapshot(windows=windows)
        result = await policy.evaluate(NotBlankStr("agent-001"), snapshot)
        assert result.eligible is False


# ── TrendPruningPolicy ──────────────────────────────────────────


@pytest.mark.unit
class TestTrendPruningPolicyConfig:
    """TrendPruningPolicyConfig validation."""

    def test_defaults(self) -> None:
        config = TrendPruningPolicyConfig()
        assert config.minimum_data_points_per_window == 5
        assert config.metric_name == "quality_score"


@pytest.mark.unit
class TestTrendPruningPolicy:
    """TrendPruningPolicy evaluation logic."""

    async def test_ineligible_empty_trends(self) -> None:
        policy = TrendPruningPolicy(TrendPruningPolicyConfig())
        snapshot = make_performance_snapshot(trends=())
        result = await policy.evaluate(NotBlankStr("agent-001"), snapshot)
        assert result.eligible is False

    async def test_ineligible_mixed_directions(self) -> None:
        policy = TrendPruningPolicy(TrendPruningPolicyConfig())
        trends = (
            make_trend_result(
                window_size="7d",
                direction=TrendDirection.DECLINING,
                slope=-0.5,
            ),
            make_trend_result(
                window_size="30d",
                direction=TrendDirection.STABLE,
                slope=0.0,
            ),
            make_trend_result(
                window_size="90d",
                direction=TrendDirection.DECLINING,
                slope=-0.3,
            ),
        )
        snapshot = make_performance_snapshot(trends=trends)
        result = await policy.evaluate(NotBlankStr("agent-001"), snapshot)
        assert result.eligible is False

    async def test_ineligible_insufficient_data_trends(self) -> None:
        policy = TrendPruningPolicy(
            TrendPruningPolicyConfig(minimum_data_points_per_window=5),
        )
        trends = (
            make_trend_result(
                window_size="7d",
                direction=TrendDirection.DECLINING,
                slope=-0.5,
                data_point_count=3,
            ),
            make_trend_result(
                window_size="30d",
                direction=TrendDirection.DECLINING,
                slope=-0.3,
                data_point_count=10,
            ),
            make_trend_result(
                window_size="90d",
                direction=TrendDirection.DECLINING,
                slope=-0.2,
                data_point_count=10,
            ),
        )
        snapshot = make_performance_snapshot(trends=trends)
        result = await policy.evaluate(NotBlankStr("agent-001"), snapshot)
        assert result.eligible is False

    async def test_eligible_all_three_windows_declining(self) -> None:
        policy = TrendPruningPolicy(TrendPruningPolicyConfig())
        trends = (
            make_trend_result(
                window_size="7d",
                direction=TrendDirection.DECLINING,
                slope=-0.5,
                data_point_count=10,
            ),
            make_trend_result(
                window_size="30d",
                direction=TrendDirection.DECLINING,
                slope=-0.3,
                data_point_count=15,
            ),
            make_trend_result(
                window_size="90d",
                direction=TrendDirection.DECLINING,
                slope=-0.2,
                data_point_count=20,
            ),
        )
        snapshot = make_performance_snapshot(trends=trends)
        result = await policy.evaluate(NotBlankStr("agent-001"), snapshot)
        assert result.eligible is True
        assert result.policy_name == "trend"
        assert len(result.reasons) >= 1

    async def test_filters_by_metric_name(self) -> None:
        """Only trends for the configured metric_name are evaluated."""
        policy = TrendPruningPolicy(
            TrendPruningPolicyConfig(metric_name=NotBlankStr("quality_score")),
        )
        trends = (
            make_trend_result(
                metric_name="quality_score",
                window_size="7d",
                direction=TrendDirection.DECLINING,
                slope=-0.5,
            ),
            make_trend_result(
                metric_name="cost",
                window_size="7d",
                direction=TrendDirection.IMPROVING,
                slope=0.3,
            ),
            make_trend_result(
                metric_name="quality_score",
                window_size="30d",
                direction=TrendDirection.DECLINING,
                slope=-0.3,
            ),
            make_trend_result(
                metric_name="quality_score",
                window_size="90d",
                direction=TrendDirection.DECLINING,
                slope=-0.2,
            ),
        )
        snapshot = make_performance_snapshot(trends=trends)
        result = await policy.evaluate(NotBlankStr("agent-001"), snapshot)
        assert result.eligible is True

    async def test_fewer_than_three_windows_ineligible(self) -> None:
        """Fewer than 3 qualifying trend windows means ineligible."""
        policy = TrendPruningPolicy(TrendPruningPolicyConfig())
        trends = (
            make_trend_result(
                window_size="7d",
                direction=TrendDirection.DECLINING,
                slope=-0.5,
            ),
            make_trend_result(
                window_size="30d",
                direction=TrendDirection.DECLINING,
                slope=-0.3,
            ),
        )
        snapshot = make_performance_snapshot(trends=trends)
        result = await policy.evaluate(NotBlankStr("agent-001"), snapshot)
        assert result.eligible is False

    async def test_insufficient_data_direction_treated_as_non_declining(self) -> None:
        policy = TrendPruningPolicy(TrendPruningPolicyConfig())
        trends = (
            make_trend_result(
                window_size="7d",
                direction=TrendDirection.DECLINING,
                slope=-0.5,
            ),
            make_trend_result(
                window_size="30d",
                direction=TrendDirection.INSUFFICIENT_DATA,
                slope=0.0,
            ),
            make_trend_result(
                window_size="90d",
                direction=TrendDirection.DECLINING,
                slope=-0.2,
            ),
        )
        snapshot = make_performance_snapshot(trends=trends)
        result = await policy.evaluate(NotBlankStr("agent-001"), snapshot)
        assert result.eligible is False

    async def test_scores_contain_slopes(self) -> None:
        policy = TrendPruningPolicy(TrendPruningPolicyConfig())
        trends = (
            make_trend_result(
                window_size="7d",
                direction=TrendDirection.DECLINING,
                slope=-0.5,
                data_point_count=10,
            ),
            make_trend_result(
                window_size="30d",
                direction=TrendDirection.DECLINING,
                slope=-0.3,
                data_point_count=15,
            ),
            make_trend_result(
                window_size="90d",
                direction=TrendDirection.DECLINING,
                slope=-0.2,
                data_point_count=20,
            ),
        )
        snapshot = make_performance_snapshot(trends=trends)
        result = await policy.evaluate(NotBlankStr("agent-001"), snapshot)
        assert "slope_7d" in result.scores
        assert "slope_30d" in result.scores
        assert "slope_90d" in result.scores
