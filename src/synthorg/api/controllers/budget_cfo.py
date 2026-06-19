# module-kind: controller
"""CFO cost-optimizer controller -- advisory spend analytics.

Read-only access to the :class:`~synthorg.budget.optimizer.CostOptimizer`
analytical surface: spending-anomaly detection and per-agent cost
efficiency analysis over a lookback window. The advisory complement to
the enforcing budget controllers.
"""

from datetime import UTC, datetime
from typing import Annotated, Final

from litestar import Controller, get
from litestar.datastructures import State
from litestar.params import QueryParameter

from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_read_access
from synthorg.api.rate_limits.policies import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.budget.optimizer_models import (
    AnomalyDetectionResult,
    EfficiencyAnalysis,
)
from synthorg.budget.state import cost_optimizer_of
from synthorg.budget.trends import TrendPeriod, period_to_timedelta

_DEFAULT_ANOMALY_WINDOW_COUNT: Final[int] = 5
_MIN_ANOMALY_WINDOW_COUNT: Final[int] = 2
_MAX_ANOMALY_WINDOW_COUNT: Final[int] = 1000


class BudgetCfoController(Controller):
    """Read-only CFO cost-optimization analytics."""

    path = "/budget/cfo"
    tags = ("budget",)
    guards = [require_read_access]  # noqa: RUF012

    @get(
        "/anomalies",
        guards=[
            per_op_rate_limit_from_policy("budget.cfo_anomalies"),
        ],
    )
    async def detect_anomalies(
        self,
        state: State,
        period: Annotated[
            TrendPeriod,
            QueryParameter(description="Lookback window for the anomaly scan."),
        ] = TrendPeriod.SEVEN_DAYS,
        window_count: Annotated[
            int,
            QueryParameter(
                ge=_MIN_ANOMALY_WINDOW_COUNT,
                le=_MAX_ANOMALY_WINDOW_COUNT,
                description="Number of equal time windows to divide the period into.",
            ),
        ] = _DEFAULT_ANOMALY_WINDOW_COUNT,
    ) -> ApiResponse[AnomalyDetectionResult]:
        """Detect spending anomalies over the lookback window.

        Args:
            state: Application state.
            period: Lookback window (7d, 30d, 90d).
            window_count: Number of equal windows the period is split into.

        Returns:
            Anomaly detection result envelope.
        """
        app_state: AppState = state.app_state
        now = datetime.now(UTC)
        start = now - period_to_timedelta(period)
        result = await cost_optimizer_of(app_state).detect_anomalies(
            start=start,
            end=now,
            window_count=window_count,
        )
        return ApiResponse(data=result)

    @get(
        "/efficiency",
        guards=[
            per_op_rate_limit_from_policy("budget.cfo_efficiency"),
        ],
    )
    async def analyze_efficiency(
        self,
        state: State,
        period: Annotated[
            TrendPeriod,
            QueryParameter(description="Lookback window for the efficiency analysis."),
        ] = TrendPeriod.SEVEN_DAYS,
    ) -> ApiResponse[EfficiencyAnalysis]:
        """Analyse per-agent cost efficiency over the lookback window.

        Args:
            state: Application state.
            period: Lookback window (7d, 30d, 90d).

        Returns:
            Efficiency analysis envelope.
        """
        app_state: AppState = state.app_state
        now = datetime.now(UTC)
        start = now - period_to_timedelta(period)
        result = await cost_optimizer_of(app_state).analyze_efficiency(
            start=start,
            end=now,
        )
        return ApiResponse(data=result)
