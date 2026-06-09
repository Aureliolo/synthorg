# module-kind: controller
"""Analytics budget-forecast endpoint at /analytics/forecast."""

from datetime import UTC, datetime, timedelta
from typing import Annotated

from litestar import Controller, get
from litestar.datastructures import State
from litestar.params import QueryParameter

from synthorg._core.features import require_service
from synthorg.api.controllers.analytics._shared import (
    _DEFAULT_HORIZON_DAYS,
    ForecastResponse,
    _resolve_budget_context,
)
from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_read_access
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.budget.state import BudgetStateSlice
from synthorg.budget.trends import project_daily_spend
from synthorg.observability import get_logger
from synthorg.observability.events.analytics import ANALYTICS_FORECAST_QUERIED
from synthorg.settings.state import config_resolver_of

logger = get_logger(__name__)


class AnalyticsForecastController(Controller):
    """Budget spend projection."""

    path = "/analytics"
    tags = ("analytics",)

    @get(
        "/forecast",
        guards=[
            require_read_access,
            per_op_rate_limit_from_policy("analytics.forecast"),
        ],
    )
    async def get_forecast(
        self,
        state: State,
        horizon_days: Annotated[
            int,
            QueryParameter(
                ge=1,
                le=90,
                description="Projection horizon in days",
            ),
        ] = _DEFAULT_HORIZON_DAYS,
    ) -> ApiResponse[ForecastResponse]:
        """Return budget spend projection.

        Fetches records from the lookback period (equal to
        horizon_days), then computes average daily spend from
        the span of records found. Confidence reflects data
        density within the lookback window.

        Args:
            state: Application state.
            horizon_days: Number of days to project forward.

        Returns:
            Forecast data envelope.
        """
        app_state: AppState = state.app_state
        now = datetime.now(UTC)
        lookback_start = now - timedelta(days=horizon_days)

        records = await require_service(
            app_state.slice(BudgetStateSlice).cost_tracker, "Cost Tracker"
        ).get_records(
            start=lookback_start,
            end=now,
        )
        budget = await _resolve_budget_context(app_state, now=now)

        forecast = project_daily_spend(
            records,
            horizon_days=horizon_days,
            budget_total_monthly=budget.monthly,
            budget_remaining=budget.remaining,
            now=now,
        )

        logger.debug(
            ANALYTICS_FORECAST_QUERIED,
            horizon_days=horizon_days,
            projected_total=forecast.projected_total,
            days_until_exhausted=forecast.days_until_exhausted,
        )

        budget_cfg = await config_resolver_of(app_state).get_budget_config()
        return ApiResponse(
            data=ForecastResponse(
                horizon_days=horizon_days,
                projected_total=forecast.projected_total,
                daily_projections=forecast.daily_projections,
                days_until_exhausted=forecast.days_until_exhausted,
                confidence=forecast.confidence,
                avg_daily_spend=forecast.avg_daily_spend,
                currency=budget_cfg.currency,
            ),
        )
