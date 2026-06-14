"""Pre-flight cost-forecast lifecycle service.

Owns the forecast read/write path the ``/budget/forecasts`` controller
used to drive directly against :class:`CostForecastRepository`. Keeping
the repository, forecaster, and budget config behind this service stops
the controller from reaching across the persistence boundary and gives
the MCP / REST surfaces one tested entry point for the
generate / get / approve / reject / raise-ceiling flow.
"""

# module-kind: service

from typing import NoReturn
from uuid import UUID

from synthorg.budget._cost_window import ClockFn, utc_now
from synthorg.budget.config import BudgetConfig
from synthorg.budget.errors import RunHardCeilingTooLowError
from synthorg.budget.forecast_models import Forecast, ForecastDecision
from synthorg.budget.forecaster import BriefSignal, CostForecaster
from synthorg.core.domain_errors import ConflictError, ResourceNotFoundError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.budget import (
    BUDGET_FORECAST_APPROVED,
    BUDGET_FORECAST_GENERATED,
    BUDGET_FORECAST_REJECTED,
    BUDGET_HARD_CEILING_RAISED,
)
from synthorg.persistence.cost_forecast_protocol import CostForecastRepository


def _raise_not_found(forecast_id: UUID, *, suffix: str = "") -> NoReturn:
    """Raise :class:`ResourceNotFoundError` with the canonical resource shape.

    Raises:
        ResourceNotFoundError: Always.
    """
    resource = "cost forecast" if not suffix else f"cost forecast {suffix}"
    msg = f"{resource} {str(forecast_id)!r} not found"
    raise ResourceNotFoundError(msg)


class BudgetForecastService:
    """Generate + transition pre-flight cost forecasts behind the boundary."""

    def __init__(
        self,
        *,
        repo: CostForecastRepository,
        forecaster: CostForecaster,
        budget_config: BudgetConfig,
        clock: ClockFn | None = None,
    ) -> None:
        """Wire the forecast repository, forecaster, and budget config.

        Args:
            repo: Persistence for stored forecasts.
            forecaster: Estimator that turns a brief signal into a forecast.
            budget_config: Active budget config (supplies the currency).
            clock: UTC-now seam for decision timestamps; defaults to the
                shared :func:`utc_now`.
        """
        self._repo = repo
        self._forecaster = forecaster
        self._budget_config = budget_config
        self._clock: ClockFn = clock if clock is not None else utc_now
        self._logger = get_logger(__name__)

    async def generate(
        self,
        *,
        brief_text: NotBlankStr,
        role_skeleton: tuple[NotBlankStr, ...],
        model_assignments: dict[NotBlankStr, NotBlankStr],
        estimated_turns_per_role: float | None,
    ) -> Forecast:
        """Generate a fresh pending forecast for a brief and persist it.

        Returns:
            The stored :class:`Forecast`.
        """
        signal = BriefSignal(
            brief_text=brief_text,
            role_skeleton=role_skeleton,
            model_assignments=model_assignments,
            currency=self._budget_config.currency,
            estimated_turns_per_role=estimated_turns_per_role,
        )
        forecast = await self._forecaster.forecast(signal)
        await self._repo.save(forecast)
        self._logger.info(
            BUDGET_FORECAST_GENERATED,
            forecast_id=str(forecast.forecast_id),
            brief_hash=forecast.brief_hash,
            estimated_cost=forecast.estimated_cost,
        )
        return forecast

    async def get_or_404(self, forecast_id: UUID) -> Forecast:
        """Return a stored forecast or raise :class:`ResourceNotFoundError`.

        Returns:
            The stored :class:`Forecast`.

        Raises:
            ResourceNotFoundError: When no forecast has that id.
        """
        forecast = await self._repo.get(forecast_id)
        if forecast is None:
            _raise_not_found(forecast_id)
        return forecast

    async def approve(
        self,
        forecast_id: UUID,
        *,
        decided_by: NotBlankStr,
        ceiling_amount: float | None,
    ) -> Forecast:
        """Approve a pending forecast and return the updated row.

        Returns:
            The approved :class:`Forecast`.

        Raises:
            ResourceNotFoundError: When no pending forecast has that id.
        """
        transitioned = await self._repo.transition_if(
            forecast_id,
            ForecastDecision.PENDING,
            ForecastDecision.APPROVED,
            decided_by=decided_by,
            ceiling_amount=ceiling_amount,
        )
        if not transitioned:
            _raise_not_found(forecast_id, suffix="(pending)")
        forecast = await self.get_or_404(forecast_id)
        self._logger.info(
            BUDGET_FORECAST_APPROVED,
            forecast_id=str(forecast_id),
            decided_by=decided_by,
            ceiling_amount=ceiling_amount,
        )
        return forecast

    async def reject(
        self,
        forecast_id: UUID,
        *,
        decided_by: NotBlankStr,
    ) -> Forecast:
        """Reject a pending forecast and return the updated row.

        Returns:
            The rejected :class:`Forecast`.

        Raises:
            ResourceNotFoundError: When no pending forecast has that id.
        """
        transitioned = await self._repo.transition_if(
            forecast_id,
            ForecastDecision.PENDING,
            ForecastDecision.REJECTED,
            decided_by=decided_by,
        )
        if not transitioned:
            _raise_not_found(forecast_id, suffix="(pending)")
        forecast = await self.get_or_404(forecast_id)
        self._logger.info(
            BUDGET_FORECAST_REJECTED,
            forecast_id=str(forecast_id),
            decided_by=decided_by,
        )
        return forecast

    async def raise_ceiling(
        self,
        forecast_id: UUID,
        *,
        new_ceiling: float,
        accumulated_cost: float,
    ) -> Forecast:
        """Raise a parked run's hard ceiling so the engine can resume.

        Returns:
            The updated :class:`Forecast` with the cleared halt context.

        Raises:
            RunHardCeilingTooLowError: When ``new_ceiling`` would re-halt
                the run immediately (``<= accumulated_cost``).
            ResourceNotFoundError: When no forecast has that id.
            ConflictError: When the forecast is not in a halted state.
        """
        if new_ceiling <= accumulated_cost:
            msg = (
                f"new_ceiling {new_ceiling} must be strictly greater"
                f" than accumulated_cost {accumulated_cost}"
            )
            raise RunHardCeilingTooLowError(
                msg,
                requested_ceiling=new_ceiling,
                accumulated_cost=accumulated_cost,
                currency=self._budget_config.currency,
            )
        forecast = await self.get_or_404(forecast_id)
        if forecast.halt_context is None:
            msg = (
                f"Forecast {forecast_id} is not in a halted state; there is"
                f" no parked run to resume by raising the ceiling"
            )
            raise ConflictError(msg)
        updated = forecast.model_copy(
            update={
                "ceiling_amount": new_ceiling,
                "halt_context": None,
                "updated_at": self._clock(),
            },
        )
        await self._repo.save(updated)
        self._logger.info(
            BUDGET_HARD_CEILING_RAISED,
            forecast_id=str(forecast_id),
            new_ceiling=new_ceiling,
            accumulated_cost=accumulated_cost,
        )
        return updated


__all__ = ["BudgetForecastService"]
