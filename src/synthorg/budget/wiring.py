# module-kind: code
"""Build the shared :class:`BudgetEnforcer` from its collaborators.

One builder, because an enforcer CAPTURES its cost tracker: anything that
swaps the tracker a deployment records into without rebuilding the enforcer
over it leaves the enforcer bounding a total nothing writes to, so no limit
can ever fire. A recording harness installs its own ledger for the length
of a cell and had exactly that gap, invisible for as long as it passed no
enforcer at all.

Takes its collaborators rather than an ``AppState`` so it stays below the
API layer: the composition roots read their own slices and hand the pieces
over.
"""

from collections.abc import Mapping

from synthorg.budget.config import BudgetConfig
from synthorg.budget.enforcer import BudgetEnforcer
from synthorg.budget.quota import DegradationConfig
from synthorg.budget.quota_tracker import QuotaTracker
from synthorg.budget.risk_tracker import RiskTracker
from synthorg.budget.tracker import CostTracker
from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.security.risk_scorer import DefaultRiskScorer


def build_budget_enforcer(
    *,
    budget_config: BudgetConfig,
    cost_tracker: CostTracker | None,
    quota_tracker: QuotaTracker | None,
    risk_tracker: RiskTracker | None,
    notification_dispatcher: NotificationDispatcher | None,
    degradation_configs: Mapping[str, DegradationConfig] | None = None,
) -> BudgetEnforcer | None:
    """Build the enforcer bounding spend into *cost_tracker*.

    Args:
        budget_config: The limits and thresholds in force.
        cost_tracker: The tracker whose total the limits are measured
            against. ``None`` (a persistence-less boot) leaves the whole
            enforcer absent rather than bounding nothing.
        quota_tracker: Provider-level quota enforcement, or ``None``.
        risk_tracker: Risk tracking service, or ``None``.
        notification_dispatcher: Where a threshold alert is sent, or
            ``None``.
        degradation_configs: Per-provider degradation strategies, or
            ``None``.

    Returns:
        The enforcer, or ``None`` when there is no tracker to bound.
    """
    if cost_tracker is None:
        return None
    return BudgetEnforcer(
        budget_config=budget_config,
        cost_tracker=cost_tracker,
        quota_tracker=quota_tracker,
        degradation_configs=degradation_configs,
        risk_tracker=risk_tracker,
        risk_scorer=DefaultRiskScorer(),
        notification_dispatcher=notification_dispatcher,
    )


__all__ = ["build_budget_enforcer"]
