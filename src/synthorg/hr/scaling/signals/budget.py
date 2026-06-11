"""Budget signal source -- reads cost metrics from budget tracker."""

from datetime import UTC, datetime

from synthorg.budget.spending_summary import SpendingSummary
from synthorg.core.types import NotBlankStr
from synthorg.hr.scaling.models import ScalingSignal
from synthorg.observability import get_logger
from synthorg.observability.events.hr import HR_SCALING_SIGNAL_COLLECTION_DEGRADED

logger = get_logger(__name__)

_SOURCE_NAME = NotBlankStr("budget")


class BudgetSignalSource:
    """Read-only adapter over the budget subsystem.

    Converts ``SpendingSummary`` into scaling signals:
    ``burn_rate_percent``, ``alert_level``.
    """

    @property
    def name(self) -> NotBlankStr:
        """Source identifier."""
        return _SOURCE_NAME

    async def collect(
        self,
        agent_ids: tuple[NotBlankStr, ...],  # noqa: ARG002
        *,
        summary: SpendingSummary | None = None,
    ) -> tuple[ScalingSignal, ...]:
        """Collect budget signals from a spending summary.

        Args:
            agent_ids: Active agent IDs (not used directly).
            summary: Current spending summary from CostTracker.

        Returns:
            Budget signals: burn_rate_percent, alert_level.
        """
        now = datetime.now(UTC)

        if summary is None:
            logger.warning(
                HR_SCALING_SIGNAL_COLLECTION_DEGRADED,
                source="budget",
                reason="no_spending_summary",
            )
            return (
                ScalingSignal(
                    name=NotBlankStr("burn_rate_percent"),
                    value=100.0,
                    source=_SOURCE_NAME,
                    timestamp=now,
                ),
                ScalingSignal(
                    name=NotBlankStr("alert_level"),
                    value=3.0,
                    source=_SOURCE_NAME,
                    timestamp=now,
                ),
            )

        # Map alert levels to numeric values for threshold comparison.
        alert_map = {
            "normal": 0.0,
            "warning": 1.0,
            "critical": 2.0,
            "hard_stop": 3.0,
        }
        alert_key = summary.alert_level.value
        if alert_key not in alert_map:
            logger.warning(
                HR_SCALING_SIGNAL_COLLECTION_DEGRADED,
                source="budget",
                reason="unknown_alert_level",
                alert_level=alert_key,
            )
        alert_value = alert_map.get(alert_key, alert_map["hard_stop"])

        return (
            ScalingSignal(
                name=NotBlankStr("burn_rate_percent"),
                value=round(summary.budget_used_percent, 4),
                source=_SOURCE_NAME,
                timestamp=now,
            ),
            ScalingSignal(
                name=NotBlankStr("alert_level"),
                value=alert_value,
                source=_SOURCE_NAME,
                timestamp=now,
            ),
        )
