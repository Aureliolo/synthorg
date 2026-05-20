"""Pre-flight cost forecast + Pareto frontier controllers.

Endpoints:

* ``POST /budget/forecast`` -- generate a pending forecast for a brief.
* ``GET /budget/forecasts/{forecast_id}`` -- retrieve a stored forecast.
* ``POST /budget/forecasts/{forecast_id}/approve`` -- operator approval.
* ``POST /budget/forecasts/{forecast_id}/reject`` -- operator rejection.
* ``POST /budget/forecasts/{forecast_id}/raise_ceiling`` -- raise the
  per-run hard ceiling after a parked run so the engine can resume.
* ``GET /budget/pareto`` -- current cost / quality frontier.
"""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from litestar import Controller, get, post
from litestar.datastructures import State  # noqa: TC002
from pydantic import BaseModel, ConfigDict, Field

from synthorg.api.guards import require_read_access
from synthorg.api.path_params import PathId
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.budget.errors import RunHardCeilingTooLowError
from synthorg.budget.forecast_models import Forecast, ForecastDecision
from synthorg.budget.forecaster import BriefSignal
from synthorg.budget.pareto import ParetoFrontier
from synthorg.core.domain_errors import (
    ServiceUnavailableError,
    resource_not_found,
)
from synthorg.core.error_taxonomy import ErrorCode
from synthorg.core.types import NotBlankStr  # noqa: TC001 -- runtime by Pydantic
from synthorg.observability import get_logger
from synthorg.observability.events.budget import (
    BUDGET_FORECAST_APPROVED,
    BUDGET_FORECAST_GENERATED,
    BUDGET_FORECAST_REJECTED,
    BUDGET_HARD_CEILING_RAISED,
)
from synthorg.persistence.cost_forecast_protocol import (  # noqa: TC001
    CostForecastRepository,
)

logger = get_logger(__name__)


class ForecastRequest(BaseModel):
    """POST /budget/forecast payload."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    brief_text: NotBlankStr = Field(description="Brief body to estimate")
    role_skeleton: tuple[NotBlankStr, ...] = Field(
        description="Ordered role ids participating in the run",
    )
    model_assignments: dict[NotBlankStr, NotBlankStr] = Field(
        default_factory=dict,
        description="Optional per-role model id (canonical alias)",
    )
    estimated_turns_per_role: float | None = Field(
        default=None,
        gt=0,
        description="Optional per-role turn estimate",
    )


class ApproveRequest(BaseModel):
    """POST /budget/forecasts/{id}/approve payload."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    decided_by: NotBlankStr = Field(description="Operator identifier")
    ceiling_amount: float | None = Field(
        default=None,
        ge=0.0,
        description="Approved per-run hard ceiling (None to inherit setting)",
    )


class RejectRequest(BaseModel):
    """POST /budget/forecasts/{id}/reject payload."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    decided_by: NotBlankStr = Field(description="Operator identifier")


class RaiseCeilingRequest(BaseModel):
    """POST /budget/forecasts/{id}/raise_ceiling payload."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    new_ceiling: float = Field(
        gt=0.0,
        description="New hard ceiling (must be > accumulated cost at park)",
    )
    accumulated_cost: float = Field(
        ge=0.0,
        description=(
            "Accumulated cost at the moment of parking, supplied by the"
            " operator UI so the endpoint can reject ceilings that would"
            " re-halt the run immediately on resume"
        ),
    )


def _raise_not_found(forecast_id: str, *, suffix: str = "") -> None:
    """Raise NotFoundError with the canonical resource shape."""
    resource = "cost forecast" if not suffix else f"cost forecast {suffix}"
    raise resource_not_found(resource, forecast_id)


def _require_repo(state: State) -> CostForecastRepository:
    """Return the cost forecast repo or raise 503."""
    app_state: AppState = state.app_state
    repo = app_state.cost_forecast_repo
    if repo is None:
        msg = "Cost forecast repository not configured"
        raise ServiceUnavailableError(msg)
    return repo


class ForecastBudgetController(Controller):
    """Endpoints exposing the pre-flight forecast + Pareto frontier."""

    path = "/budget"
    guards = (require_read_access,)
    tags = ("budget", "forecast")

    @post("/forecast", status_code=201)
    async def create_forecast(
        self,
        data: ForecastRequest,
        state: State,
    ) -> Forecast:
        """Generate a fresh pending forecast for a brief."""
        app_state: AppState = state.app_state
        forecaster = app_state.cost_forecaster
        repo = app_state.cost_forecast_repo
        budget = app_state.budget_config
        if forecaster is None or repo is None or budget is None:
            msg = "Cost forecaster not configured"
            raise ServiceUnavailableError(msg)
        signal = BriefSignal(
            brief_text=data.brief_text,
            role_skeleton=data.role_skeleton,
            model_assignments=data.model_assignments,
            currency=budget.currency,
            estimated_turns_per_role=data.estimated_turns_per_role,
        )
        forecast = await forecaster.forecast(signal)
        await repo.save(forecast)
        logger.info(
            BUDGET_FORECAST_GENERATED,
            forecast_id=str(forecast.forecast_id),
            brief_hash=forecast.brief_hash,
            estimated_cost=forecast.estimated_cost,
        )
        return forecast

    @get("/forecasts/{forecast_id:str}")
    async def get_forecast(
        self,
        forecast_id: Annotated[str, PathId],
        state: State,
    ) -> Forecast:
        """Retrieve a stored forecast by id."""
        repo = _require_repo(state)
        forecast = await repo.get(UUID(forecast_id))
        if forecast is None:
            _raise_not_found(forecast_id)
            raise AssertionError  # unreachable; _raise_not_found always raises
        return forecast

    @post("/forecasts/{forecast_id:str}/approve")
    async def approve_forecast(
        self,
        forecast_id: Annotated[str, PathId],
        data: ApproveRequest,
        state: State,
    ) -> Forecast:
        """Approve a pending forecast; releases the work pipeline."""
        repo = _require_repo(state)
        target = UUID(forecast_id)
        transitioned = await repo.transition_if(
            target,
            ForecastDecision.PENDING,
            ForecastDecision.APPROVED,
            decided_by=data.decided_by,
            ceiling_amount=data.ceiling_amount,
        )
        if not transitioned:
            _raise_not_found(forecast_id, suffix="(pending)")
        forecast = await repo.get(target)
        if forecast is None:  # pragma: no cover
            _raise_not_found(forecast_id)
            raise AssertionError
        logger.info(
            BUDGET_FORECAST_APPROVED,
            forecast_id=forecast_id,
            decided_by=data.decided_by,
            ceiling_amount=data.ceiling_amount,
        )
        return forecast

    @post("/forecasts/{forecast_id:str}/reject")
    async def reject_forecast(
        self,
        forecast_id: Annotated[str, PathId],
        data: RejectRequest,
        state: State,
    ) -> Forecast:
        """Reject a pending forecast; terminates the work item."""
        repo = _require_repo(state)
        target = UUID(forecast_id)
        transitioned = await repo.transition_if(
            target,
            ForecastDecision.PENDING,
            ForecastDecision.REJECTED,
            decided_by=data.decided_by,
        )
        if not transitioned:
            _raise_not_found(forecast_id, suffix="(pending)")
        forecast = await repo.get(target)
        if forecast is None:  # pragma: no cover
            _raise_not_found(forecast_id)
            raise AssertionError
        logger.info(
            BUDGET_FORECAST_REJECTED,
            forecast_id=forecast_id,
            decided_by=data.decided_by,
        )
        return forecast

    @post("/forecasts/{forecast_id:str}/raise_ceiling")
    async def raise_ceiling(
        self,
        forecast_id: Annotated[str, PathId],
        data: RaiseCeilingRequest,
        state: State,
    ) -> Forecast:
        """Raise a parked run's hard ceiling so the engine can resume."""
        app_state: AppState = state.app_state
        budget = app_state.budget_config
        if budget is None:
            msg = "Budget configuration not available"
            raise ServiceUnavailableError(msg)
        if data.new_ceiling <= data.accumulated_cost:
            msg = (
                f"new_ceiling {data.new_ceiling} must be strictly greater"
                f" than accumulated_cost {data.accumulated_cost}"
            )
            raise RunHardCeilingTooLowError(
                msg,
                requested_ceiling=data.new_ceiling,
                accumulated_cost=data.accumulated_cost,
                currency=budget.currency,
            )
        repo = _require_repo(state)
        target = UUID(forecast_id)
        forecast = await repo.get(target)
        if forecast is None:
            _raise_not_found(forecast_id)
            raise AssertionError
        updated = forecast.model_copy(
            update={
                "ceiling_amount": data.new_ceiling,
                "updated_at": datetime.now(UTC),
            },
        )
        await repo.save(updated)
        logger.info(
            BUDGET_HARD_CEILING_RAISED,
            forecast_id=forecast_id,
            new_ceiling=data.new_ceiling,
            accumulated_cost=data.accumulated_cost,
        )
        return updated

    @get("/pareto")
    async def get_pareto(self, state: State) -> ParetoFrontier:
        """Return the current cost / quality frontier."""
        app_state: AppState = state.app_state
        analyzer = app_state.pareto_analyzer
        if analyzer is None:
            return ParetoFrontier(
                points=(),
                generated_at=datetime.now(UTC),
                baseline_window_size=1,
                source="stub:calibrated-v1",
            )
        return await analyzer.analyse()


_ = ErrorCode  # keep import; ErrorCode used by future refinements
