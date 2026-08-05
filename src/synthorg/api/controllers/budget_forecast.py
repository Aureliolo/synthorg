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

from uuid import UUID

from litestar import Controller, get, post
from litestar.datastructures import State
from pydantic import BaseModel, ConfigDict, Field

from synthorg.api.dto import ApiResponse
from synthorg.api.guards import require_read_access, require_write_access
from synthorg.api.path_params import PathId
from synthorg.api.rate_limits import per_op_rate_limit_from_policy
from synthorg.api.state import AppState
from synthorg.budget.forecast_models import Forecast
from synthorg.budget.forecast_service import BudgetForecastService
from synthorg.budget.pareto import (
    ParetoFrontier,
)
from synthorg.budget.state import BudgetStateSlice
from synthorg.core.actor_context import resolve_decided_by
from synthorg.core.domain_errors import ServiceUnavailableError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.budget import BUDGET_FORECAST_UNAVAILABLE

logger = get_logger(__name__)


class ForecastRequest(BaseModel):
    """POST /budget/forecast payload."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    brief_text: NotBlankStr = Field(description="Brief body to estimate")
    project: NotBlankStr = Field(description="Project the work would land in")
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
    """POST /budget/forecasts/{id}/approve payload.

    The decider is read from the authenticated actor, never the body: an
    approval releases the gated work item and spends real money, so who
    authorised it cannot be a string the caller chose.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    ceiling_amount: float | None = Field(
        default=None,
        ge=0.0,
        description="Approved per-run hard ceiling (None to inherit setting)",
    )


class ForecastRejectRequest(BaseModel):
    """POST /budget/forecasts/{id}/reject payload.

    Empty for the same reason as its approve counterpart: the decider comes
    from the authenticated actor.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")


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


def _require_service(state: State) -> BudgetForecastService:
    """Return the forecast service or raise 503.

    Returns:
        ``BudgetForecastService`` instance.

    Raises:
        ServiceUnavailableError: Raised on the corresponding failure path.
    """
    app_state: AppState = state.app_state
    service = app_state.slice(BudgetStateSlice).forecast_service
    if service is None:
        logger.warning(BUDGET_FORECAST_UNAVAILABLE, has_forecast_service=False)
        msg = "Cost forecaster not configured"
        raise ServiceUnavailableError(msg)
    return service


class ForecastBudgetController(Controller):
    """Endpoints exposing the pre-flight forecast + Pareto frontier."""

    path = "/budget"
    guards = (require_read_access,)
    tags = ("budget", "forecast")

    @post(
        "/forecast",
        status_code=201,
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("budget.forecast_create", key="user"),
        ],
    )
    async def create_forecast(
        self,
        data: ForecastRequest,
        state: State,
    ) -> ApiResponse[Forecast]:
        """Generate a fresh pending forecast for a brief.

        Returns:
            ``Forecast`` instance.

        Raises:
            ServiceUnavailableError: Raised on the corresponding failure path.
        """
        service = _require_service(state)
        return ApiResponse(
            data=await service.generate(
                brief_text=data.brief_text,
                project=data.project,
                requested_by=NotBlankStr(resolve_decided_by()),
                role_skeleton=data.role_skeleton,
                model_assignments=data.model_assignments,
                estimated_turns_per_role=data.estimated_turns_per_role,
            )
        )

    @get("/forecasts/{forecast_id:str}")
    async def get_forecast(
        self,
        forecast_id: PathId,
        state: State,
    ) -> ApiResponse[Forecast]:
        """Retrieve a stored forecast by id.

        Returns:
            ``Forecast`` instance.
        """
        service = _require_service(state)
        return ApiResponse(data=await service.get_or_404(UUID(forecast_id)))

    @post(
        "/forecasts/{forecast_id:str}/approve",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("budget.forecast_decide", key="user"),
        ],
    )
    async def approve_forecast(
        self,
        forecast_id: PathId,
        data: ForecastApproveRequest,
        state: State,
    ) -> ApiResponse[Forecast]:
        """Approve a pending forecast; releases the work pipeline.

        Returns:
            ``Forecast`` instance.

        Raises:
            ActorContextMissingError: When no authenticated actor is bound;
                an approval that spends money must be attributable.
        """
        service = _require_service(state)
        return ApiResponse(
            data=await service.approve(
                UUID(forecast_id),
                decided_by=NotBlankStr(resolve_decided_by()),
                ceiling_amount=data.ceiling_amount,
            )
        )

    @post(
        "/forecasts/{forecast_id:str}/reject",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("budget.forecast_decide", key="user"),
        ],
    )
    async def reject_forecast(
        self,
        forecast_id: PathId,
        data: ForecastRejectRequest,
        state: State,
    ) -> ApiResponse[Forecast]:
        """Reject a pending forecast; terminates the work item.

        Returns:
            ``Forecast`` instance.

        Raises:
            ActorContextMissingError: When no authenticated actor is bound.
        """
        del data  # the decider is the authenticated actor, not a body field
        service = _require_service(state)
        return ApiResponse(
            data=await service.reject(
                UUID(forecast_id),
                decided_by=NotBlankStr(resolve_decided_by()),
            )
        )

    @post(
        "/forecasts/{forecast_id:str}/raise_ceiling",
        guards=[
            require_write_access,
            per_op_rate_limit_from_policy("budget.forecast_raise_ceiling", key="user"),
        ],
    )
    async def raise_ceiling(
        self,
        forecast_id: PathId,
        data: RaiseCeilingRequest,
        state: State,
    ) -> ApiResponse[Forecast]:
        """Raise a parked run's hard ceiling so the engine can resume.

        Returns:
            ``Forecast`` instance.

        Raises:
            ServiceUnavailableError: Raised on the corresponding failure path.
            RunHardCeilingTooLowError: Raised on the corresponding failure path.
            ConflictError: Raised on the corresponding failure path.
        """
        service = _require_service(state)
        return ApiResponse(
            data=await service.raise_ceiling(
                UUID(forecast_id),
                new_ceiling=data.new_ceiling,
                accumulated_cost=data.accumulated_cost,
            )
        )

    @get("/pareto")
    async def get_pareto(self, state: State) -> ApiResponse[ParetoFrontier]:
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
        return ApiResponse(data=await analyzer.analyse())
