"""Tests for TheilSenTrendStrategy."""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import TrendDirection
from synthorg.hr.performance.theil_sen_strategy import TheilSenTrendStrategy

NOW = datetime(2026, 3, 10, 12, 0, 0, tzinfo=UTC)


@pytest.mark.unit
class TestTheilSenTrendStrategy:
    """TheilSenTrendStrategy trend detection."""

    def _make_strategy(
        self,
        *,
        min_data_points: int = 5,
        improving_threshold: float = 0.05,
        declining_threshold: float = -0.05,
    ) -> TheilSenTrendStrategy:
        return TheilSenTrendStrategy(
            min_data_points=min_data_points,
            improving_threshold=improving_threshold,
            declining_threshold=declining_threshold,
        )

    def test_name(self) -> None:
        assert self._make_strategy().name == "theil_sen"

    def test_monotonically_increasing_improving(self) -> None:
        """Monotonically increasing data -> IMPROVING."""
        strategy = self._make_strategy()
        # Each day the value increases by 1.0 -> slope ~1.0 per day
        values = tuple((NOW + timedelta(days=i), float(i)) for i in range(10))

        result = strategy.detect(
            metric_name=NotBlankStr("quality"),
            values=values,
            window_size=NotBlankStr("30d"),
        )

        assert result.direction == TrendDirection.IMPROVING
        assert result.slope > 0.0
        assert result.data_point_count == 10

    def test_monotonically_decreasing_declining(self) -> None:
        """Monotonically decreasing data -> DECLINING."""
        strategy = self._make_strategy()
        values = tuple((NOW + timedelta(days=i), 10.0 - float(i)) for i in range(10))

        result = strategy.detect(
            metric_name=NotBlankStr("quality"),
            values=values,
            window_size=NotBlankStr("30d"),
        )

        assert result.direction == TrendDirection.DECLINING
        assert result.slope < 0.0

    def test_flat_data_stable(self) -> None:
        """Constant values -> STABLE."""
        strategy = self._make_strategy()
        values = tuple((NOW + timedelta(days=i), 5.0) for i in range(10))

        result = strategy.detect(
            metric_name=NotBlankStr("quality"),
            values=values,
            window_size=NotBlankStr("30d"),
        )

        assert result.direction == TrendDirection.STABLE
        assert result.slope == 0.0

    def test_less_than_min_data_points_insufficient(self) -> None:
        """< min_data_points -> INSUFFICIENT_DATA."""
        strategy = self._make_strategy(min_data_points=5)
        values = tuple((NOW + timedelta(days=i), float(i)) for i in range(4))

        result = strategy.detect(
            metric_name=NotBlankStr("quality"),
            values=values,
            window_size=NotBlankStr("30d"),
        )

        assert result.direction == TrendDirection.INSUFFICIENT_DATA
        assert result.slope == 0.0
        assert result.data_point_count == 4

    def test_exactly_min_data_points_works(self) -> None:
        """Exactly min_data_points -> produces result (not INSUFFICIENT_DATA)."""
        strategy = self._make_strategy(min_data_points=5)
        values = tuple((NOW + timedelta(days=i), float(i)) for i in range(5))

        result = strategy.detect(
            metric_name=NotBlankStr("quality"),
            values=values,
            window_size=NotBlankStr("30d"),
        )

        assert result.direction != TrendDirection.INSUFFICIENT_DATA
        assert result.data_point_count == 5

    def test_single_outlier_tolerance(self) -> None:
        """Theil-Sen is robust to a single outlier."""
        strategy = self._make_strategy(min_data_points=5)
        # Linear increase with one outlier at index 3
        values_list: list[tuple[datetime, float]] = [
            (NOW + timedelta(days=i), float(i)) for i in range(10)
        ]
        # Inject a massive outlier
        values_list[3] = (NOW + timedelta(days=3), 100.0)
        values = tuple(values_list)

        result = strategy.detect(
            metric_name=NotBlankStr("quality"),
            values=values,
            window_size=NotBlankStr("30d"),
        )

        # Despite the outlier, median slope should still be ~1.0 (IMPROVING)
        assert result.direction == TrendDirection.IMPROVING
        # Slope should be close to 1.0 (not wildly distorted)
        assert 0.5 < result.slope < 2.0

    def test_near_threshold_stable(self) -> None:
        """Slope near threshold boundaries -> STABLE."""
        strategy = self._make_strategy(
            improving_threshold=0.05,
            declining_threshold=-0.05,
        )
        # Very gentle slope that should be within the stable band
        values = tuple((NOW + timedelta(days=i), float(i) * 0.01) for i in range(10))

        result = strategy.detect(
            metric_name=NotBlankStr("quality"),
            values=values,
            window_size=NotBlankStr("30d"),
        )

        assert result.direction == TrendDirection.STABLE
        assert abs(result.slope) <= 0.05

    def test_result_fields_populated(self) -> None:
        """All TrendResult fields are correctly set."""
        strategy = self._make_strategy()
        values = tuple((NOW + timedelta(days=i), float(i)) for i in range(7))

        result = strategy.detect(
            metric_name=NotBlankStr("cost"),
            values=values,
            window_size=NotBlankStr("7d"),
        )

        assert result.metric_name == "cost"
        assert result.window_size == "7d"
        assert result.data_point_count == 7

    @pytest.mark.parametrize(
        "min_pts",
        [1, 3, 10],
        ids=["min_1", "min_3", "min_10"],
    )
    def test_custom_min_data_points(self, min_pts: int) -> None:
        """Different min_data_points thresholds respected."""
        strategy = self._make_strategy(min_data_points=min_pts)
        # Provide exactly min_pts - 1 data points
        values = tuple((NOW + timedelta(days=i), float(i)) for i in range(min_pts - 1))

        result = strategy.detect(
            metric_name=NotBlankStr("quality"),
            values=values,
            window_size=NotBlankStr("30d"),
        )

        assert result.direction == TrendDirection.INSUFFICIENT_DATA

    def test_same_timestamp_points_ignored(self) -> None:
        """Points at the same timestamp are ignored for slope calc."""
        strategy = self._make_strategy(min_data_points=2)
        # All points at same time -> no valid slopes -> STABLE
        values = tuple((NOW, float(i)) for i in range(5))

        result = strategy.detect(
            metric_name=NotBlankStr("quality"),
            values=values,
            window_size=NotBlankStr("30d"),
        )

        assert result.direction == TrendDirection.STABLE
        assert result.slope == 0.0
