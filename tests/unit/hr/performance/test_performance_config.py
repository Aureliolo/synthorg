"""Tests for PerformanceConfig validation."""

import pytest
from pydantic import ValidationError

from synthorg.core.types import NotBlankStr
from synthorg.hr.performance.config import PerformanceConfig


@pytest.mark.unit
class TestTrendThresholdOrdering:
    """The improving threshold must sit strictly above the declining one."""

    def test_defaults_are_ordered(self) -> None:
        cfg = PerformanceConfig()
        assert cfg.improving_threshold > cfg.declining_threshold

    @pytest.mark.parametrize(
        ("improving", "declining"),
        [(0.05, 0.05), (-0.1, 0.1)],
        ids=["equal", "inverted"],
    )
    def test_unordered_thresholds_rejected(
        self,
        improving: float,
        declining: float,
    ) -> None:
        # An inverted or equal pair leaves a slope that is neither improving
        # nor declining, or both at once, so the trend label stops meaning
        # anything.
        with pytest.raises(ValidationError, match="improving_threshold"):
            PerformanceConfig(
                improving_threshold=improving,
                declining_threshold=declining,
            )


@pytest.mark.unit
class TestWindowConfiguration:
    """Rolling-window labels and the aggregation floor."""

    def test_defaults(self) -> None:
        cfg = PerformanceConfig()
        assert cfg.windows
        assert cfg.min_data_points >= 1

    def test_empty_windows_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PerformanceConfig(windows=())

    def test_custom_windows_accepted(self) -> None:
        cfg = PerformanceConfig(windows=(NotBlankStr("7d"), NotBlankStr("30d")))
        assert cfg.windows == ("7d", "30d")

    def test_zero_min_data_points_rejected(self) -> None:
        with pytest.raises(ValidationError):
            PerformanceConfig(min_data_points=0)


@pytest.mark.unit
class TestScoringFieldsAreGone:
    """The tracker reads the oracle's verdict; it configures no scorer."""

    @pytest.mark.parametrize(
        "field",
        [
            "quality_judge_model",
            "quality_judge_provider",
            "quality_ci_weight",
            "collaboration_weights",
            "llm_sampling_rate",
        ],
    )
    def test_scoring_field_is_refused(self, field: str) -> None:
        # ``extra="forbid"`` turns a stale operator config into a loud
        # startup failure rather than a silently ignored key.
        with pytest.raises(ValidationError, match="extra_forbidden"):
            PerformanceConfig(**{field: 0.5})  # type: ignore[arg-type]
