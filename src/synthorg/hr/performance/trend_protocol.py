"""Trend detection strategy protocol.

Defines the interface for pluggable trend detection strategies
that analyze metric time series (see Agents design page, D12).
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.hr.performance.models import TrendResult  # noqa: TC001

if TYPE_CHECKING:
    from pydantic import AwareDatetime


@runtime_checkable
class TrendDetectionStrategy(Protocol):
    """Strategy for detecting trends in metric time series.

    Implementations analyze sequences of (timestamp, value) pairs
    to determine whether a metric is improving, stable, or declining.
    """

    @property
    def name(self) -> str:
        """Human-readable strategy name."""
        ...

    def detect(
        self,
        *,
        metric_name: NotBlankStr,
        values: tuple[tuple[AwareDatetime, float], ...],
        window_size: NotBlankStr,
    ) -> TrendResult:
        """Detect the trend direction in a metric time series.

        Args:
            metric_name: Name of the metric being analyzed.
            values: Time series data as (timestamp, value) pairs.
            window_size: Time window label for context.

        Returns:
            Trend detection result with direction and slope.
        """
        ...
