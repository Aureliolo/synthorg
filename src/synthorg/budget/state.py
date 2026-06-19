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
from synthorg.budget.forecast_service import BudgetForecastService
from synthorg.budget.forecaster import CostForecaster
from synthorg.budget.pareto import ParetoAnalyzer
from synthorg.budget.quota_tracker import QuotaTracker
from synthorg.budget.risk_tracker import RiskTracker
from synthorg.budget.tracker import CostTracker
from synthorg.budget.version_service import BudgetConfigVersionsService
from synthorg.persistence.benchmark_score_protocol import (
    BenchmarkScoreRepository,
)
from synthorg.persistence.cost_forecast_protocol import (
    CostForecastRepository,
)


class BudgetStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the budget feature."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    cost_tracker: CostTracker | None = None
    budget_enforcer: BudgetAffordabilityChecker | None = None
    quota_tracker: QuotaTracker | None = None
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
