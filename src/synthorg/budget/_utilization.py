"""Shared monthly-spend + budget-utilization computation.

The pre-flight enforcer and the cost optimizer both resolve
spend-since-billing-period-start and derive a utilization percentage from
it. The two-line ``billing_period_start`` + ``get_total_cost`` lookup and
the ``cost / total_monthly * 100`` ratio live here so both call sites
share one definition instead of re-deriving the arithmetic.
"""

from datetime import datetime

from synthorg.budget.billing import billing_period_start
from synthorg.budget.config import BudgetConfig
from synthorg.budget.tracker import CostTracker


async def compute_monthly_cost(
    config: BudgetConfig,
    cost_tracker: CostTracker,
    *,
    now: datetime | None = None,
) -> float:
    """Return spend accrued since the current billing period started.

    Args:
        config: Budget configuration (supplies ``reset_day``).
        cost_tracker: Cost tracker queried for the period total.
        now: Reference instant for the billing-period start; defaults to
            the cost tracker's clock.

    Returns:
        Total cost since the period start.
    """
    period_start = billing_period_start(config.reset_day, now=now)
    return await cost_tracker.get_total_cost(start=period_start)


def utilization_pct(monthly_cost: float, config: BudgetConfig) -> float:
    """Return ``monthly_cost`` as a percentage of the monthly budget.

    Args:
        monthly_cost: Spend so far this period.
        config: Budget configuration. The caller must ensure
            ``total_monthly > 0`` (the disabled case is handled upstream).

    Returns:
        The utilization percentage (``0`` to ``100+``).
    """
    return monthly_cost / config.total_monthly * 100
