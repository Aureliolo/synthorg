"""Budget signal aggregator.

Wraps budget analytics pure functions to produce an OrgBudgetSummary
with spend patterns, category breakdowns, and forecasts.
"""

from datetime import datetime

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.meta.models import OrgBudgetSummary
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.meta import (
    META_SIGNAL_AGGREGATION_COMPLETED,
    META_SIGNAL_AGGREGATION_FAILED,
)

logger = get_logger(__name__)

_EMPTY = OrgBudgetSummary(
    total_spend=0.0,
    productive_ratio=0.0,
    coordination_ratio=0.0,
    system_ratio=0.0,
    forecast_confidence=0.0,
    orchestration_overhead=0.0,
)


class BudgetSignalAggregator:
    """Aggregates budget analytics into org-wide summaries.

    Args:
        cost_record_provider: Callable returning cost records for a window.
        budget_total_monthly: Monthly budget ceiling in the configured currency.
        budget_remaining_provider: Callable returning remaining budget.
    """

    def __init__(
        self,
        *,
        cost_record_provider: object,
        budget_total_monthly: float = 0.0,
        budget_remaining_provider: object = None,
    ) -> None:
        self._cost_record_provider = cost_record_provider
        self._budget_total_monthly = budget_total_monthly
        self._budget_remaining_provider = budget_remaining_provider

    @property
    def domain(self) -> NotBlankStr:
        """Signal domain name.

        Returns:
            ``NotBlankStr`` instance.
        """
        return NotBlankStr("budget")

    async def aggregate(
        self,
        *,
        since: datetime,
        until: datetime,
    ) -> OrgBudgetSummary:
        """Aggregate budget signals for the time window.

        Args:
            since: Start of observation window.
            until: End of observation window.

        Returns:
            Org-wide budget summary.
        """
        _ = since, until  # Will be used by real implementation.
        try:
            # Placeholder: real implementation will call budget pure
            # functions (bucket_cost_records, project_daily_spend,
            # CategoryBreakdown) with actual cost records.
            logger.info(
                META_SIGNAL_AGGREGATION_COMPLETED,
                domain="budget",
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            log_exception_redacted(
                logger, META_SIGNAL_AGGREGATION_FAILED, exc, domain="budget"
            )
        return _EMPTY
