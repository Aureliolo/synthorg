"""Summary report strategy."""

from pydantic import JsonValue

from synthorg.client.models import SimulationMetrics


class SummaryReport:
    """High-level snapshot report.

    Produces a compact dict suitable for dashboard KPI cards or a
    quick CLI overview.
    """

    async def generate_report(
        self,
        metrics: SimulationMetrics,
    ) -> dict[str, JsonValue]:
        """Return a dashboard-friendly metrics summary."""
        return {
            "format": "summary",
            "totals": {
                "requirements": metrics.total_requirements,
                "tasks_created": metrics.total_tasks_created,
                "accepted": metrics.tasks_accepted,
                "rejected": metrics.tasks_rejected,
                "reworked": metrics.tasks_reworked,
            },
            "rates": {
                "acceptance_rate": metrics.acceptance_rate,
                "rework_rate": metrics.rework_rate,
                "avg_review_rounds": metrics.avg_review_rounds,
            },
        }
