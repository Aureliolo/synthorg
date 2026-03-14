"""Tests for MultiWindowStrategy."""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.hr.performance.multi_window_strategy import MultiWindowStrategy

from .conftest import make_task_metric

NOW = datetime(2026, 3, 10, 12, 0, 0, tzinfo=UTC)


@pytest.mark.unit
class TestMultiWindowStrategy:
    """MultiWindowStrategy windowing and aggregation."""

    def _make_strategy(
        self,
        *,
        windows: tuple[str, ...] = ("7d", "30d", "90d"),
        min_data_points: int = 5,
    ) -> MultiWindowStrategy:
        return MultiWindowStrategy(
            windows=windows,
            min_data_points=min_data_points,
        )

    def test_name(self) -> None:
        assert self._make_strategy().name == "multi_window"

    def test_min_data_points_property(self) -> None:
        strategy = self._make_strategy(min_data_points=3)
        assert strategy.min_data_points == 3

    def test_records_spanning_all_windows(self) -> None:
        """Records in 7d, 30d, 90d -> 3 WindowMetrics returned."""
        strategy = self._make_strategy(min_data_points=1)
        records = tuple(
            make_task_metric(
                completed_at=NOW - timedelta(days=i),
                cost_usd=1.0,
                duration_seconds=60.0,
                tokens_used=100,
                quality_score=8.0,
            )
            for i in range(60)
        )

        result = strategy.compute_windows(records, now=NOW)

        assert len(result) == 3
        assert result[0].window_size == "7d"
        assert result[1].window_size == "30d"
        assert result[2].window_size == "90d"
        # 7d window should have fewer records than 30d/90d
        assert result[0].data_point_count <= result[1].data_point_count
        assert result[1].data_point_count <= result[2].data_point_count

    def test_records_only_in_7d(self) -> None:
        """Records only in 7d window -> 30d and 90d also include them."""
        strategy = self._make_strategy(min_data_points=1)
        records = tuple(
            make_task_metric(
                completed_at=NOW - timedelta(days=1),
                cost_usd=1.0,
                duration_seconds=60.0,
                tokens_used=100,
            )
            for _ in range(3)
        )

        result = strategy.compute_windows(records, now=NOW)

        assert result[0].data_point_count == 3  # 7d
        assert result[1].data_point_count == 3  # 30d (same records)
        assert result[2].data_point_count == 3  # 90d (same records)

    def test_insufficient_data_none_averages(self) -> None:
        """Below min_data_points -> None for averages and success_rate."""
        strategy = self._make_strategy(min_data_points=5)
        records = tuple(
            make_task_metric(
                completed_at=NOW - timedelta(days=1),
                cost_usd=1.0,
                duration_seconds=60.0,
                tokens_used=100,
            )
            for _ in range(3)
        )

        result = strategy.compute_windows(records, now=NOW)

        # All windows have 3 records < min_data_points=5
        for window in result:
            assert window.data_point_count == 3
            assert window.avg_quality_score is None
            assert window.avg_cost_per_task is None
            assert window.avg_completion_time_seconds is None
            assert window.avg_tokens_per_task is None
            assert window.success_rate is None

    def test_empty_records(self) -> None:
        """No records -> zero counts, None aggregates."""
        strategy = self._make_strategy()

        result = strategy.compute_windows((), now=NOW)

        assert len(result) == 3
        for window in result:
            assert window.data_point_count == 0
            assert window.tasks_completed == 0
            assert window.tasks_failed == 0
            assert window.avg_quality_score is None
            assert window.avg_cost_per_task is None
            assert window.success_rate is None

    def test_success_and_failure_counts(self) -> None:
        """Mixed success/failure records -> correct counts."""
        strategy = self._make_strategy(min_data_points=1)
        records = (
            make_task_metric(
                completed_at=NOW - timedelta(hours=1),
                is_success=True,
            ),
            make_task_metric(
                completed_at=NOW - timedelta(hours=2),
                is_success=True,
            ),
            make_task_metric(
                completed_at=NOW - timedelta(hours=3),
                is_success=False,
            ),
        )

        result = strategy.compute_windows(records, now=NOW)

        assert result[0].tasks_completed == 2
        assert result[0].tasks_failed == 1
        assert result[0].success_rate == pytest.approx(2 / 3, abs=0.001)

    def test_averages_computed_with_enough_data(self) -> None:
        """At min_data_points -> averages are computed."""
        strategy = self._make_strategy(min_data_points=5)
        records = tuple(
            make_task_metric(
                completed_at=NOW - timedelta(hours=i + 1),
                cost_usd=2.0,
                duration_seconds=100.0,
                tokens_used=500,
                quality_score=7.0,
            )
            for i in range(5)
        )

        result = strategy.compute_windows(records, now=NOW)

        window = result[0]  # 7d window
        assert window.avg_quality_score == 7.0
        assert window.avg_cost_per_task == 2.0
        assert window.avg_completion_time_seconds == 100.0
        assert window.avg_tokens_per_task == 500.0

    def test_quality_score_average_excludes_none(self) -> None:
        """Quality scores that are None are excluded from average."""
        strategy = self._make_strategy(min_data_points=2)
        records = (
            make_task_metric(
                completed_at=NOW - timedelta(hours=1),
                quality_score=8.0,
            ),
            make_task_metric(
                completed_at=NOW - timedelta(hours=2),
                quality_score=None,
            ),
            make_task_metric(
                completed_at=NOW - timedelta(hours=3),
                quality_score=6.0,
            ),
        )

        result = strategy.compute_windows(records, now=NOW)

        # Average of 8.0 and 6.0 only (None excluded)
        assert result[0].avg_quality_score == 7.0

    @pytest.mark.parametrize(
        ("windows", "expected_count"),
        [
            (("7d",), 1),
            (("7d", "30d"), 2),
            (("7d", "30d", "90d", "365d"), 4),
        ],
        ids=["single_window", "two_windows", "four_windows"],
    )
    def test_parametrize_window_configs(
        self,
        windows: tuple[str, ...],
        expected_count: int,
    ) -> None:
        strategy = self._make_strategy(windows=windows, min_data_points=1)
        records = (make_task_metric(completed_at=NOW - timedelta(hours=1)),)

        result = strategy.compute_windows(records, now=NOW)

        assert len(result) == expected_count

    def test_invalid_window_format_raises(self) -> None:
        """Invalid window format (e.g. 'weekly') raises ValueError."""
        strategy = self._make_strategy(windows=("weekly",))
        records = (make_task_metric(completed_at=NOW - timedelta(hours=1)),)

        with pytest.raises(ValueError, match="Unrecognized window size"):
            strategy.compute_windows(records, now=NOW)

    def test_old_records_excluded_from_short_window(self) -> None:
        """Records older than 7d excluded from 7d window, included in 30d."""
        strategy = self._make_strategy(
            windows=("7d", "30d"),
            min_data_points=1,
        )
        records = (
            make_task_metric(completed_at=NOW - timedelta(days=2)),
            make_task_metric(completed_at=NOW - timedelta(days=15)),
        )

        result = strategy.compute_windows(records, now=NOW)

        assert result[0].data_point_count == 1  # 7d: only 2-day-old
        assert result[1].data_point_count == 2  # 30d: both
