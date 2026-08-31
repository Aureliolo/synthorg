"""Budget feature state slice.

Holds the cost-dial services: the cost tracker, forecaster + forecast
repo, Pareto analyzer, benchmark-score provider, the active budget
config, and the automated cost-report service. The tracker is
constructor-injected; the cost-dial services are wired best-effort
after persistence connects. All fields are ``None`` until wired;
readers guard accordingly.
"""

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice, require_service
from synthorg.api.state_slices import AppStateSliceMixin
from synthorg.budget.affordability import BudgetAffordabilityChecker
from synthorg.budget.automated_reports import AutomatedReportService
from synthorg.budget.benchmark_protocol import (
    BenchmarkScoreProvider,
)
from synthorg.budget.call_analytics import CallAnalyticsService
from synthorg.budget.config import BudgetConfig
from synthorg.budget.enforcer import BudgetEnforcer
from synthorg.budget.forecast_service import BudgetForecastService
from synthorg.budget.forecaster import CostForecaster
from synthorg.budget.optimizer import CostOptimizer
from synthorg.budget.pareto import ParetoAnalyzer
from synthorg.budget.quota_poller import QuotaPoller
from synthorg.budget.quota_tracker import QuotaTracker
from synthorg.budget.risk_tracker import RiskTracker
from synthorg.budget.tracker import CostTracker
from synthorg.budget.version_service import BudgetConfigVersionsService
from synthorg.observability import get_logger
from synthorg.observability.events.budget import (
    BUDGET_ENFORCER_UNWIRED,
    BUDGET_ENFORCER_WIRED,
    BUDGET_ENFORCER_WRONG_TYPE,
)
from synthorg.persistence.benchmark_score_protocol import (
    BenchmarkScoreRepository,
)
from synthorg.persistence.cost_forecast_protocol import (
    CostForecastRepository,
)

logger = get_logger(__name__)


class BudgetStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the budget feature."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    cost_tracker: CostTracker | None = None
    budget_enforcer: BudgetAffordabilityChecker | None = None
    quota_tracker: QuotaTracker | None = None
    quota_poller: QuotaPoller | None = None
    risk_tracker: RiskTracker | None = None
    call_analytics_service: CallAnalyticsService | None = None
    cost_forecaster: CostForecaster | None = None
    cost_forecast_repo: CostForecastRepository | None = None
    forecast_service: BudgetForecastService | None = None
    benchmark_score_repo: BenchmarkScoreRepository | None = None
    pareto_analyzer: ParetoAnalyzer | None = None
    benchmark_provider: BenchmarkScoreProvider | None = None
    budget_config: BudgetConfig | None = None
    report_service: AutomatedReportService | None = None
    budget_versions_service: BudgetConfigVersionsService | None = None
    cost_optimizer: CostOptimizer | None = None


def cost_tracker_of(app_state: AppStateSliceMixin) -> CostTracker:
    """Resolve the cost tracker from its slice, or raise 503.

    Returns:
        The wired cost tracker.
    """
    return require_service(
        app_state.slice(BudgetStateSlice).cost_tracker, "Cost Tracker"
    )


def budget_versions_service_of(
    app_state: AppStateSliceMixin,
) -> BudgetConfigVersionsService:
    """Resolve the budget-versions service from its slice, or raise 503.

    Returns:
        The wired budget-config versions service.
    """
    return require_service(
        app_state.slice(BudgetStateSlice).budget_versions_service,
        "Budget Versions Service",
    )


def cost_optimizer_of(app_state: AppStateSliceMixin) -> CostOptimizer:
    """Resolve the CFO cost optimizer from its slice, or raise 503.

    Returns:
        The wired cost optimizer.
    """
    return require_service(
        app_state.slice(BudgetStateSlice).cost_optimizer, "Cost Optimizer"
    )


def budget_enforcer_of(app_state: AppStateSliceMixin) -> BudgetEnforcer | None:
    """Resolve the concrete budget enforcer from its slice, or ``None``.

    The slice field is typed against the narrow ``BudgetAffordabilityChecker``
    protocol so the engine layer never imports the heavy concrete enforcer;
    this narrows it back to the ``BudgetEnforcer`` ``AgentEngine`` needs for
    monthly, daily, project and run-hard-ceiling enforcement (task-level
    ``budget_limit``/``hard_token_ceiling`` already enforce via the bare
    task-only fallback with no enforcer wired). Unlike ``budget/adoption.py``'s
    best-effort ``isinstance`` fan-out (safe to skip a holder that doesn't
    match), this accessor's return value decides whether that broader
    enforcement exists at all, so a wired-but-wrong-type value is logged at
    ERROR (not a value with a harmless fallback) rather than silently read
    the same as "not wired".

    Returns:
        The wired enforcer, or ``None`` when unwired.
    """
    enforcer = app_state.slice(BudgetStateSlice).budget_enforcer
    if enforcer is None:
        logger.debug(BUDGET_ENFORCER_UNWIRED)
        return None
    if isinstance(enforcer, BudgetEnforcer):
        logger.debug(BUDGET_ENFORCER_WIRED)
        return enforcer
    logger.error(
        BUDGET_ENFORCER_WRONG_TYPE,
        expected_type="BudgetEnforcer",
        actual_type=type(enforcer).__name__,
        reason="enforcement_disabled",
    )
    return None
