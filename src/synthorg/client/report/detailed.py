"""Detailed report strategy."""

from pydantic import JsonValue

from synthorg.client.models import SimulationMetrics


class DetailedReport:
    """Expanded report with totals, rates, and per-round breakdown.

    Suitable for post-run analysis or executive summaries where
    both the headline numbers and the per-round trajectory matter.
    """

    async def generate_report(
        self,
        metrics: SimulationMetrics,
    ) -> dict[str, JsonValue]:
        """Return a detailed metrics report with narrative summary."""
        summary = self._format_summary(metrics)
        # Re-emit each TypedDict snapshot as a plain JSON object so the
        # report stays a precise ``dict[str, JsonValue]`` (a TypedDict is
        # not assignable to ``JsonValue`` under list invariance).
        per_round: list[JsonValue] = [
            {
                "round_number": entry["round_number"],
                "total_requirements": entry["total_requirements"],
                "tasks_created": entry["tasks_created"],
                "accepted": entry["accepted"],
                "rejected": entry["rejected"],
            }
            for entry in metrics.round_metrics
        ]
        return {
            "format": "detailed",
            "summary": summary,
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
            "per_round": per_round,
        }

    @staticmethod
    def _format_summary(metrics: SimulationMetrics) -> str:
        return (
            f"Processed {metrics.total_requirements} requirements, "
            f"created {metrics.total_tasks_created} tasks. "
            f"Acceptance rate {metrics.acceptance_rate:.1%}, "
            f"rework rate {metrics.rework_rate:.1%}, "
            f"average review rounds {metrics.avg_review_rounds:.1f}."
        )
