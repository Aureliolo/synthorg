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
from typing import NoReturn
from uuid import UUID

from litestar import Controller, get, post
from litestar.datastructures import State  # noqa: TC002
from pydantic import BaseModel, ConfigDict, Field

from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.path_params import PathId  # noqa: TC001 -- runtime annotation
from synthorg.api.state import AppState  # noqa: TC001
from synthorg.budget.errors import RunHardCeilingTooLowError
from synthorg.budget.forecast_models import Forecast, ForecastDecision
from synthorg.budget.forecaster import BriefSignal
from synthorg.budget.pareto import (  # noqa: TC001 -- runtime return annotation
    ParetoFrontier,
)
from synthorg.budget.state import BudgetStateSlice
from synthorg.core.domain_errors import (
    ConflictError,
    ResourceNotFoundError,
    ServiceUnavailableError,
)
from synthorg.core.types import NotBlankStr  # noqa: TC001 -- runtime by Pydantic
from synthorg.observability import get_logger
from synthorg.observability.events.budget import (
    BUDGET_FORECAST_APPROVED,
    BUDGET_FORECAST_GENERATED,
    BUDGET_FORECAST_REJECTED,
    BUDGET_FORECAST_UNAVAILABLE,
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
        min_length=1,
        description="Ordered role ids participating in the run (non-empty)",
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


class ForecastApproveRequest(BaseModel):
    """POST /budget/forecasts/{id}/approve payload."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    decided_by: NotBlankStr = Field(description="Operator identifier")
    ceiling_amount: float | None = Field(
        default=None,
        ge=0.0,
        description="Approved per-run hard ceiling (None to inherit setting)",
    )


class ForecastRejectRequest(BaseModel):
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
            " operator UI. The endpoint rejects ceilings that would"
            " re-halt the run immediately on resume with a typed"
            " RunHardCeilingTooLowError (richer than a generic 422), so"
            " the cross-field check stays in the handler rather than a"
            " model validator that would shadow that typed error"
        ),
    )


def _raise_not_found(forecast_id: str, *, suffix: str = "") -> NoReturn:
    """Raise ResourceNotFoundError with the canonical resource shape.

    Raises:
        ResourceNotFoundError: Raised on the corresponding failure path.
    """
    resource = "cost forecast" if not suffix else f"cost forecast {suffix}"
    msg = f"{resource} {forecast_id!r} not found"
    raise ResourceNotFoundError(msg)


def _require_repo(state: State) -> CostForecastRepository:
    """Return the cost forecast repo or raise 503.

    Returns:
        ``CostForecastRepository`` instance.

    Raises:
        ServiceUnavailableError: Raised on the corresponding failure path.
    """
    app_state: AppState = state.app_state
    repo = app_state.slice(BudgetStateSlice).cost_forecast_repo
    if repo is None:
        msg = "Cost forecast repository not configured"
        raise ServiceUnavailableError(msg)
    return repo


class ForecastBudgetController(Controller):
    """Endpoints exposing the pre-flight forecast + Pareto frontier."""

    path = "/budget"
    guards = (require_read_access,)
    tags = ("budget", "forecast")

    @post("/forecast", status_code=201, guards=[require_write_access])
    async def create_forecast(
        self,
        data: ForecastRequest,
        state: State,
    ) -> Forecast:
        """Generate a fresh pending forecast for a brief.

        Returns:
            ``Forecast`` instance.

        Raises:
            ServiceUnavailableError: Raised on the corresponding failure path.
        """
        app_state: AppState = state.app_state
        forecaster = app_state.slice(BudgetStateSlice).cost_forecaster
        repo = app_state.slice(BudgetStateSlice).cost_forecast_repo
        budget = app_state.slice(BudgetStateSlice).budget_config
        if forecaster is None or repo is None or budget is None:
            logger.warning(
                BUDGET_FORECAST_UNAVAILABLE,
                has_forecaster=forecaster is not None,
                has_repo=repo is not None,
                has_budget_config=budget is not None,
            )
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
        forecast_id: PathId,
        state: State,
    ) -> Forecast:
        """Retrieve a stored forecast by id.

        Returns:
            ``Forecast`` instance.
        """
        repo = _require_repo(state)
        forecast = await repo.get(UUID(forecast_id))
        if forecast is None:
            _raise_not_found(forecast_id)
        return forecast

    @post("/forecasts/{forecast_id:str}/approve", guards=[require_write_access])
    async def approve_forecast(
        self,
        forecast_id: PathId,
        data: ForecastApproveRequest,
        state: State,
    ) -> Forecast:
        """Approve a pending forecast; releases the work pipeline.

        Returns:
            ``Forecast`` instance.
        """
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
        logger.info(
            BUDGET_FORECAST_APPROVED,
            forecast_id=forecast_id,
            decided_by=data.decided_by,
            ceiling_amount=data.ceiling_amount,
        )
        return forecast

    @post("/forecasts/{forecast_id:str}/reject", guards=[require_write_access])
    async def reject_forecast(
        self,
        forecast_id: PathId,
        data: ForecastRejectRequest,
        state: State,
    ) -> Forecast:
        """Reject a pending forecast; terminates the work item.

        Returns:
            ``Forecast`` instance.
        """
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
        logger.info(
            BUDGET_FORECAST_REJECTED,
            forecast_id=forecast_id,
            decided_by=data.decided_by,
        )
        return forecast

    @post("/forecasts/{forecast_id:str}/raise_ceiling", guards=[require_write_access])
    async def raise_ceiling(
        self,
        forecast_id: PathId,
        data: RaiseCeilingRequest,
        state: State,
    ) -> Forecast:
        """Raise a parked run's hard ceiling so the engine can resume.

        Returns:
            ``Forecast`` instance.

        Raises:
            ServiceUnavailableError: Raised on the corresponding failure path.
            RunHardCeilingTooLowError: Raised on the corresponding failure path.
            ConflictError: Raised on the corresponding failure path.
        """
        app_state: AppState = state.app_state
        budget = app_state.slice(BudgetStateSlice).budget_config
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
        if forecast.halt_context is None:
            msg = (
                f"Forecast {forecast_id} is not in a halted state; there is"
                f" no parked run to resume by raising the ceiling"
            )
            raise ConflictError(msg)
        updated = forecast.model_copy(
            update={
                "ceiling_amount": data.new_ceiling,
                "halt_context": None,
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
        """Return the current cost / quality frontier.

        Returns:
            ``ParetoFrontier`` instance.

        Raises:
            ServiceUnavailableError: Raised on the corresponding failure path.
        """
        app_state: AppState = state.app_state
        analyzer = app_state.slice(BudgetStateSlice).pareto_analyzer
        if analyzer is None:
            msg = "Pareto analyzer not configured"
            raise ServiceUnavailableError(msg)
        return await analyzer.analyse()
