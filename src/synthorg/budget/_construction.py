# module-kind: code
"""Budget feature construction-phase state-slice wiring."""

from typing import TYPE_CHECKING

from synthorg.api.state import AppState
from synthorg.budget.state import BudgetStateSlice

if TYPE_CHECKING:
    # Cycle breaker: ``api.construction_wiring`` pulls the
    # ``communication.config`` engine<->communication cold-import cycle, so
    # ``ConstructionDeps`` is named for signatures only.
    from synthorg.api.construction_wiring import ConstructionDeps


def wire_construction(app_state: AppState, deps: ConstructionDeps) -> None:
    """Populate the budget slice (cost tracker + quota / risk / analytics).

    The quota + risk trackers and the per-call analytics service are pure
    in-memory and need no connected backend, so they are built here at the
    construction phase. The cost-dial startup hook threads the quota + risk
    trackers into the ``BudgetEnforcer``, and the automated report service
    reads the risk tracker off this slice.
    """
    from synthorg.budget.call_analytics import (  # noqa: PLC0415
        CallAnalyticsService,
    )
    from synthorg.budget.optimizer import CostOptimizer  # noqa: PLC0415
    from synthorg.budget.quota_tracker import QuotaTracker  # noqa: PLC0415
    from synthorg.budget.risk_tracker import RiskTracker  # noqa: PLC0415

    budget_cfg = deps.effective_config.budget
    cost_tracker = deps.phase1.cost_tracker
    call_analytics = (
        CallAnalyticsService(
            cost_tracker=cost_tracker,
            config=budget_cfg.call_analytics,
            notification_dispatcher=deps.notification_dispatcher,
        )
        if cost_tracker is not None
        else None
    )
    cost_optimizer = (
        CostOptimizer(cost_tracker=cost_tracker, budget_config=budget_cfg)
        if cost_tracker is not None
        else None
    )
    app_state.swap_slice(
        BudgetStateSlice.model_construct(
            cost_tracker=cost_tracker,
            quota_tracker=QuotaTracker(subscriptions=budget_cfg.subscriptions),
            risk_tracker=RiskTracker(risk_budget_config=budget_cfg.risk_budget),
            call_analytics_service=call_analytics,
            cost_optimizer=cost_optimizer,
        )
    )
