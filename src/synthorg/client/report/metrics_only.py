"""Metrics-only report strategy."""

from typing import Any

from synthorg.client.models import SimulationMetrics


class MetricsOnlyReport:
    """Raw metrics dump.

    Returns the ``SimulationMetrics`` model serialized to a dict
    with no extra wrapping, for callers that want direct access to
    the aggregated numbers.
    """

    async def generate_report(
        self,
        metrics: SimulationMetrics,
    ) -> dict[str, Any]:
        """Return the Pydantic model dump of metrics."""
        return metrics.model_dump()
