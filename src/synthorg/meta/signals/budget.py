"""Budget signal aggregator.

Wraps budget analytics pure functions to produce an OrgBudgetSummary
with spend patterns, category breakdowns, and forecasts.
"""

from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from typing import Final

from synthorg.budget.category_analytics import build_category_breakdown
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.trends import project_daily_spend
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.meta.signal_models import OrgBudgetSummary
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.meta import (
    META_SIGNAL_AGGREGATION_COMPLETED,
    META_SIGNAL_AGGREGATION_FAILED,
)

logger = get_logger(__name__)

_FORECAST_HORIZON_DAYS: Final[int] = 30

CostRecordProvider = Callable[[datetime, datetime], Awaitable[Sequence[CostRecord]]]
BudgetRemainingProvider = Callable[[], Awaitable[float]]

_EMPTY = OrgBudgetSummary(
    total_spend=0.0,
    productive_ratio=0.0,
    coordination_ratio=0.0,
    system_ratio=0.0,
    forecast_confidence=0.0,
    orchestration_overhead=0.0,
)


def _ratio(part: float, whole: float) -> float:
    """Return ``part / whole`` clamped to ``[0, 1]`` (0 when whole is 0).

    Returns:
        The clamped ratio.
    """
    if whole <= 0.0:
        return 0.0
    return min(max(part / whole, 0.0), 1.0)


def _overhead(coordination_tokens: int, productive_tokens: int) -> float:
    """Coordination/productive token ratio (0 when no productive tokens).

    Returns:
        The coordination-to-productive token ratio.
    """
    if productive_tokens <= 0:
        return 0.0
    return coordination_tokens / productive_tokens


class BudgetSignalAggregator:
    """Aggregates budget analytics into org-wide summaries.

    Args:
        cost_record_provider: Async callable returning the cost records
            in a ``[since, until)`` window.
        budget_total_monthly: Monthly budget ceiling in the configured currency.
        budget_remaining_provider: Optional async callable returning the
            remaining budget; enables a ``days_until_exhausted`` forecast.
    """

    def __init__(
        self,
        *,
        cost_record_provider: CostRecordProvider,
        budget_total_monthly: float = 0.0,
        budget_remaining_provider: BudgetRemainingProvider | None = None,
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

        Fetches the window's cost records, builds a per-category cost /
        token breakdown, and projects forward to derive the spend ratios,
        forecast confidence, and orchestration overhead. Degrades to an
        empty summary on any failure so the signals facade stays up.

        Args:
            since: Start of observation window.
            until: End of observation window.

        Returns:
            Org-wide budget summary.
        """
        try:
            records = await self._cost_record_provider(since, until)
            if not records:
                logger.info(META_SIGNAL_AGGREGATION_COMPLETED, domain="budget")
                return _EMPTY
            return await self._summarise(tuple(records), until)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            log_exception_redacted(
                logger, META_SIGNAL_AGGREGATION_FAILED, exc, domain="budget"
            )
            return _EMPTY

    async def _summarise(
        self,
        records: Sequence[CostRecord],
        until: datetime,
    ) -> OrgBudgetSummary:
        """Build the summary from a non-empty record set.

        Returns:
            The org-wide budget summary.
        """
        breakdown = build_category_breakdown(records)
        total = breakdown.total_cost
        remaining: float | None = None
        if self._budget_remaining_provider is not None:
            remaining = await self._budget_remaining_provider()
        forecast = project_daily_spend(
            records,
            horizon_days=_FORECAST_HORIZON_DAYS,
            budget_total_monthly=self._budget_total_monthly,
            budget_remaining=remaining,
            now=until,
        )
        summary = OrgBudgetSummary(
            total_spend=total,
            productive_ratio=_ratio(breakdown.productive_cost, total),
            coordination_ratio=_ratio(breakdown.coordination_cost, total),
            system_ratio=_ratio(breakdown.system_cost, total),
            days_until_exhausted=forecast.days_until_exhausted,
            forecast_confidence=forecast.confidence,
            orchestration_overhead=_overhead(
                breakdown.coordination_tokens,
                breakdown.productive_tokens,
            ),
        )
        logger.info(
            META_SIGNAL_AGGREGATION_COMPLETED,
            domain="budget",
            total_spend=total,
            record_count=len(records),
        )
        return summary
