"""Metrics window strategy protocol.

Defines the interface for pluggable rolling-window aggregation
strategies (see Agents design page, D11).
"""

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from synthorg.hr.performance.models import (
    TaskMetricRecord,  # noqa: TC001
    WindowMetrics,  # noqa: TC001
)

if TYPE_CHECKING:
    from pydantic import AwareDatetime


@runtime_checkable
class MetricsWindowStrategy(Protocol):
    """Strategy for computing rolling-window aggregate metrics.

    Implementations partition records into time windows and compute
    per-window aggregate statistics.
    """

    @property
    def name(self) -> str:
        """Human-readable strategy name."""
        ...

    @property
    def min_data_points(self) -> int:
        """Minimum data points required for meaningful aggregation."""
        ...

    def compute_windows(
        self,
        records: tuple[TaskMetricRecord, ...],
        *,
        now: AwareDatetime,
    ) -> tuple[WindowMetrics, ...]:
        """Compute aggregate metrics for each configured time window.

        Args:
            records: Task metric records to aggregate.
            now: Reference point for window boundaries.

        Returns:
            Aggregate metrics per time window.
        """
        ...
