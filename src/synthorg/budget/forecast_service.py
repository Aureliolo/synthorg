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
from synthorg.budget.forecast_dispatch_port import ApprovedForecastDispatcher
from synthorg.budget.forecast_models import Forecast, ForecastDecision
from synthorg.budget.forecaster import BriefSignal, CostForecaster
from synthorg.core.domain_errors import (
    ConflictError,
    ResourceNotFoundError,
    ServiceUnavailableError,
)
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.budget import (
    BUDGET_FORECAST_APPROVED,
    BUDGET_FORECAST_GENERATED,
    BUDGET_FORECAST_REDISPATCH_FAILED,
    BUDGET_FORECAST_REDISPATCHED,
    BUDGET_FORECAST_REJECTED,
    BUDGET_HARD_CEILING_RAISE_REJECTED,
    BUDGET_HARD_CEILING_RAISED,
)
from synthorg.persistence.cost_forecast_protocol import CostForecastRepository

logger = get_logger(__name__)


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
        dispatcher: ApprovedForecastDispatcher | None = None,
    ) -> None:
        """Wire the forecast repository, forecaster, and budget config.

        Args:
            repo: Persistence for stored forecasts.
            forecaster: Estimator that turns a brief signal into a forecast.
            budget_config: Active budget config (supplies the currency).
            clock: UTC-now seam for decision timestamps; defaults to the
                shared :func:`utc_now`.
            dispatcher: Runs the work an approved forecast gated. Wired
                after boot via :meth:`attach_dispatcher`, because the work
                pipeline is built later than this service.
        """
        self._repo = repo
        self._forecaster = forecaster
        self._budget_config = budget_config
        self._clock: ClockFn = clock if clock is not None else utc_now
        self._dispatcher = dispatcher

    def attach_dispatcher(self, dispatcher: ApprovedForecastDispatcher) -> None:
        """Attach the port that runs an approved forecast's gated work."""
        self._dispatcher = dispatcher

    async def generate(
        self,
        *,
        brief_text: NotBlankStr,
        project: NotBlankStr,
        requested_by: NotBlankStr,
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
            project=project,
            requested_by=requested_by,
            role_skeleton=role_skeleton,
            model_assignments=model_assignments,
            currency=self._budget_config.currency,
            estimated_turns_per_role=estimated_turns_per_role,
        )
        forecast = await self._forecaster.forecast(signal)
        await self._repo.save(forecast)
        logger.info(
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
        """Approve a pending forecast, then run the work it gated.

        Approval without dispatch is what made the automation door a
        silent dead-end: the gate accepted a brief, refused it pending a
        decision, and the decision changed nothing. Running the work is
        the decision taking effect.

        Returns:
            The approved :class:`Forecast`.

        Raises:
            ResourceNotFoundError: When no pending forecast has that id.
            DomainError: When the gated work exists but cannot be
                dispatched, so the operator learns it did not start.
        """
        # Checked before the state change, not after: the decision commits
        # atomically and there is no transition back out of APPROVED, so an
        # unwired dispatcher discovered afterwards would leave the row
        # approved, the work un-run, and a retry rejected as not-pending.
        await self._require_dispatchable(forecast_id)
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
        logger.info(
            BUDGET_FORECAST_APPROVED,
            forecast_id=str(forecast_id),
            decided_by=decided_by,
            ceiling_amount=ceiling_amount,
        )
        await self._dispatch_gated_work(forecast)
        return forecast

    async def _require_dispatchable(self, forecast_id: UUID) -> None:
        """Refuse an approval whose work could not be run once committed.

        Raises:
            ServiceUnavailableError: When the forecast holds gated work and
                no dispatcher is wired. The dispatcher attaches late in
                boot, so this window is real rather than theoretical.
        """
        forecast = await self._repo.get(forecast_id)
        if forecast is None or forecast.gated_work_item is None:
            return
        if self._dispatcher is not None:
            return
        msg = (
            f"Cost forecast {forecast_id} holds gated work but no dispatcher"
            f" is wired; approving now would drop the approved work"
        )
        logger.warning(
            BUDGET_FORECAST_REDISPATCH_FAILED,
            forecast_id=str(forecast_id),
            reason="dispatcher_unwired",
            error_type=ServiceUnavailableError.__name__,
        )
        raise ServiceUnavailableError(msg)

    async def _dispatch_gated_work(self, forecast: Forecast) -> None:
        """Run the work *forecast* gated, if it gated any.

        Raises:
            ServiceUnavailableError: When the forecast carries work but no
                dispatcher is wired to run it. Silence here is the exact
                failure the gate exists to prevent, one step later.
        """
        if forecast.gated_work_item is None:
            # Generated directly through the forecast API rather than by the
            # gate: there is no held work item, so approval is only a budget
            # decision and there is nothing to run.
            return
        if self._dispatcher is None:
            msg = (
                f"Cost forecast {forecast.forecast_id} holds gated work but no"
                f" dispatcher is wired; the approved work would be dropped"
            )
            logger.warning(
                BUDGET_FORECAST_REDISPATCH_FAILED,
                forecast_id=str(forecast.forecast_id),
                reason="dispatcher_unwired",
                error_type=ServiceUnavailableError.__name__,
            )
            raise ServiceUnavailableError(msg)
        await self._dispatcher.dispatch(forecast)
        logger.info(
            BUDGET_FORECAST_REDISPATCHED,
            forecast_id=str(forecast.forecast_id),
        )

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
        logger.info(
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
            logger.warning(
                BUDGET_HARD_CEILING_RAISE_REJECTED,
                forecast_id=str(forecast_id),
                reason="ceiling_too_low",
                new_ceiling=new_ceiling,
                accumulated_cost=accumulated_cost,
                error_type=RunHardCeilingTooLowError.__name__,
            )
            raise RunHardCeilingTooLowError(
                msg,
                requested_ceiling=new_ceiling,
                accumulated_cost=accumulated_cost,
                currency=self._budget_config.currency,
            )
        forecast = await self.get_or_404(forecast_id)
        current_ceiling = forecast.ceiling_amount
        if current_ceiling is not None and new_ceiling <= current_ceiling:
            # A raise that lands at or below the stored ceiling would clear
            # the halt while enforcement keeps the stricter value it already
            # holds, leaving the dashboard showing a number no run obeys.
            msg = (
                f"new_ceiling {new_ceiling} must be strictly greater than the"
                f" current ceiling {current_ceiling}; raising cannot lower it"
            )
            logger.warning(
                BUDGET_HARD_CEILING_RAISE_REJECTED,
                forecast_id=str(forecast_id),
                reason="ceiling_not_raised",
                new_ceiling=new_ceiling,
                current_ceiling=current_ceiling,
                error_type=RunHardCeilingTooLowError.__name__,
            )
            raise RunHardCeilingTooLowError(
                msg,
                requested_ceiling=new_ceiling,
                accumulated_cost=accumulated_cost,
                currency=self._budget_config.currency,
            )
        if forecast.halt_context is None:
            msg = (
                f"Forecast {forecast_id} is not in a halted state; there is"
                f" no parked run to resume by raising the ceiling"
            )
            logger.warning(
                BUDGET_HARD_CEILING_RAISE_REJECTED,
                forecast_id=str(forecast_id),
                reason="not_halted",
                error_type=ConflictError.__name__,
            )
            raise ConflictError(msg)
        now = self._clock()
        # Optimistic-concurrency conditional write: the read above and
        # the clear-halt write are separated by an await, so a concurrent
        # raise_ceiling could resume the run between them. The repo guards
        # on the row still being halted; a lost race surfaces the same
        # not-halted conflict rather than silently double-resuming
        # (Slot 39 CAS).
        cleared = await self._repo.raise_ceiling_if_halted(
            forecast_id,
            new_ceiling=new_ceiling,
            updated_at=now,
        )
        if not cleared:
            msg = (
                f"Forecast {forecast_id} is no longer in a halted state; a"
                f" concurrent resume won the race to raise the ceiling"
            )
            logger.warning(
                BUDGET_HARD_CEILING_RAISE_REJECTED,
                forecast_id=str(forecast_id),
                reason="concurrent_resume",
                error_type=ConflictError.__name__,
            )
            raise ConflictError(msg)
        updated = forecast.model_copy(
            update={
                "ceiling_amount": new_ceiling,
                "halt_context": None,
                "updated_at": now,
            },
        )
        logger.info(
            BUDGET_HARD_CEILING_RAISED,
            forecast_id=str(forecast_id),
            new_ceiling=new_ceiling,
            accumulated_cost=accumulated_cost,
        )
        return updated


__all__ = ["BudgetForecastService"]
