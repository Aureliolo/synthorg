"""Theil-Sen trend detection strategy (D12).

Robust non-parametric estimator for trend slopes.  Computes median
of all pairwise slopes. Pure function, no I/O, no external deps.
"""

from itertools import combinations
from typing import TYPE_CHECKING

from synthorg.hr.enums import TrendDirection
from synthorg.hr.performance.models import TrendResult
from synthorg.observability import get_logger
from synthorg.observability.events.performance import PERF_TREND_COMPUTED

if TYPE_CHECKING:
    from pydantic import AwareDatetime

    from synthorg.core.types import NotBlankStr

logger = get_logger(__name__)

# Seconds per day for timestamp normalization.
_SECONDS_PER_DAY: float = 86400.0

# Minimum time delta (in days) to consider for slope computation.
_MIN_DELTA_DAYS: float = 1e-10


def _median(values: list[float]) -> float:
    """Compute the median of a pre-sorted list of values.

    Caller must sort the list before calling this function.
    Returns 0.0 for an empty list (sentinel for no-slope cases).
    """
    n = len(values)
    if n == 0:
        return 0.0
    mid = n // 2
    if n % 2 == 0:
        return (values[mid - 1] + values[mid]) / 2.0
    return values[mid]


class TheilSenTrendStrategy:
    """Trend detection using the Theil-Sen estimator (D12).

    Computes slopes for all n*(n-1)/2 pairs of data points
    (skipping pairs with negligible time deltas) and takes the
    median as the robust slope estimate.  Timestamps are
    normalized to days for slope computation.

    Args:
        min_data_points: Minimum points required for trend detection.
        improving_threshold: Slope above which trend is IMPROVING.
        declining_threshold: Slope below which trend is DECLINING.
    """

    def __init__(
        self,
        *,
        min_data_points: int = 5,
        improving_threshold: float = 0.05,
        declining_threshold: float = -0.05,
    ) -> None:
        self._min_data_points = min_data_points
        self._improving_threshold = improving_threshold
        self._declining_threshold = declining_threshold

    @property
    def name(self) -> str:
        """Human-readable strategy name."""
        return "theil_sen"

    def detect(
        self,
        *,
        metric_name: NotBlankStr,
        values: tuple[tuple[AwareDatetime, float], ...],
        window_size: NotBlankStr,
    ) -> TrendResult:
        """Detect trend direction using Theil-Sen estimator.

        Args:
            metric_name: Name of the metric being analyzed.
            values: Time series as (timestamp, value) pairs.
            window_size: Time window label for context.

        Returns:
            Trend result with direction and slope.
        """
        count = len(values)
        if count < self._min_data_points:
            return TrendResult(
                metric_name=metric_name,
                window_size=window_size,
                direction=TrendDirection.INSUFFICIENT_DATA,
                slope=0.0,
                data_point_count=count,
            )

        # Compute all pairwise slopes.
        slopes: list[float] = []
        for (t1, v1), (t2, v2) in combinations(values, 2):
            dt_days = (t2.timestamp() - t1.timestamp()) / _SECONDS_PER_DAY
            if abs(dt_days) < _MIN_DELTA_DAYS:
                continue
            slope = (v2 - v1) / dt_days
            slopes.append(slope)

        if not slopes:
            return TrendResult(
                metric_name=metric_name,
                window_size=window_size,
                direction=TrendDirection.STABLE,
                slope=0.0,
                data_point_count=count,
            )

        slopes.sort()
        median_slope = _median(slopes)

        if median_slope > self._improving_threshold:
            direction = TrendDirection.IMPROVING
        elif median_slope < self._declining_threshold:
            direction = TrendDirection.DECLINING
        else:
            direction = TrendDirection.STABLE

        result = TrendResult(
            metric_name=metric_name,
            window_size=window_size,
            direction=direction,
            slope=round(median_slope, 6),
            data_point_count=count,
        )

        logger.debug(
            PERF_TREND_COMPUTED,
            metric=metric_name,
            window=window_size,
            direction=result.direction.value,
            slope=result.slope,
        )
        return result
