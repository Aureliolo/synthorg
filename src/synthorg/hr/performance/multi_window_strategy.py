"""Multi-window rolling metrics strategy (D11).

Computes aggregate metrics across configurable time windows simultaneously.
Returns None for aggregate values when data points < min_data_points.
"""

import re
from datetime import timedelta
from typing import TYPE_CHECKING

from synthorg.core.types import NotBlankStr
from synthorg.hr.performance.models import TaskMetricRecord, WindowMetrics
from synthorg.observability import get_logger
from synthorg.observability.events.performance import PERF_WINDOW_INSUFFICIENT_DATA

if TYPE_CHECKING:
    from pydantic import AwareDatetime

logger = get_logger(__name__)

# Pattern for parsing window size strings (e.g. '7d', '30d', '90d').
_WINDOW_PATTERN = re.compile(r"^(\d+)d$")


def _parse_window_days(window_size: str) -> int:
    """Parse a window size string into number of days.

    Args:
        window_size: Window label like '7d', '30d', '90d'.

    Returns:
        Number of days.

    Raises:
        ValueError: If the format is not recognized.
    """
    match = _WINDOW_PATTERN.match(window_size)
    if not match:
        msg = f"Unrecognized window size format: {window_size!r}. Expected '<N>d'."
        raise ValueError(msg)
    return int(match.group(1))


class MultiWindowStrategy:
    """Rolling-window metrics across multiple time windows (D11).

    Filters records by ``completed_at`` relative to ``now`` and computes
    aggregate statistics per window. Returns ``None`` for aggregate
    values when the record count is below ``min_data_points``.

    Args:
        windows: Window size labels (e.g. '7d', '30d', '90d').
        min_data_points: Minimum records for meaningful aggregation.
    """

    def __init__(
        self,
        *,
        windows: tuple[str, ...] = ("7d", "30d", "90d"),
        min_data_points: int = 5,
    ) -> None:
        self._windows = windows
        self._min_data_points = min_data_points

    @property
    def name(self) -> str:
        """Human-readable strategy name."""
        return "multi_window"

    @property
    def min_data_points(self) -> int:
        """Minimum data points required for meaningful aggregation."""
        return self._min_data_points

    def compute_windows(
        self,
        records: tuple[TaskMetricRecord, ...],
        *,
        now: AwareDatetime,
    ) -> tuple[WindowMetrics, ...]:
        """Compute aggregate metrics for each configured window.

        Args:
            records: Task metric records to aggregate.
            now: Reference point for window boundaries.

        Returns:
            One ``WindowMetrics`` per configured window.
        """
        results: list[WindowMetrics] = []
        for window_label in self._windows:
            days = _parse_window_days(window_label)
            cutoff = now - timedelta(days=days)
            window_records = tuple(r for r in records if r.completed_at >= cutoff)
            metrics = self._compute_single_window(
                window_label,
                window_records,
            )
            results.append(metrics)
        return tuple(results)

    def _compute_single_window(
        self,
        window_label: str,
        records: tuple[TaskMetricRecord, ...],
    ) -> WindowMetrics:
        """Compute metrics for a single time window."""
        count = len(records)
        completed = sum(1 for r in records if r.is_success)
        failed = count - completed
        has_enough = count >= self._min_data_points

        if not has_enough and count > 0:
            logger.debug(
                PERF_WINDOW_INSUFFICIENT_DATA,
                window=window_label,
                count=count,
                min_required=self._min_data_points,
            )

        if count == 0:
            return WindowMetrics(
                window_size=NotBlankStr(window_label),
                data_point_count=0,
                tasks_completed=0,
                tasks_failed=0,
            )

        # Compute averages and success_rate only if sufficient data.
        avg_quality = None
        avg_cost = None
        avg_time = None
        avg_tokens = None
        success_rate = None

        if has_enough:
            scored = [r.quality_score for r in records if r.quality_score is not None]
            avg_quality = sum(scored) / len(scored) if scored else None
            avg_cost = sum(r.cost_usd for r in records) / count
            avg_time = sum(r.duration_seconds for r in records) / count
            avg_tokens = sum(r.tokens_used for r in records) / count
            success_rate = completed / count

        return WindowMetrics(
            window_size=NotBlankStr(window_label),
            data_point_count=count,
            tasks_completed=completed,
            tasks_failed=failed,
            avg_quality_score=(
                round(avg_quality, 4) if avg_quality is not None else None
            ),
            avg_cost_per_task=(round(avg_cost, 4) if avg_cost is not None else None),
            avg_completion_time_seconds=(
                round(avg_time, 4) if avg_time is not None else None
            ),
            avg_tokens_per_task=(
                round(avg_tokens, 4) if avg_tokens is not None else None
            ),
            success_rate=(round(success_rate, 4) if success_rate is not None else None),
        )
