"""Pre-flight cost forecast gate at the work-entry seam.

The gate sits between an entry adapter (intake, task-board, objective,
conversational propose) and the composed work pipeline. When
``budget.forecast_required`` is enabled it refuses to dispatch a
:class:`WorkItem` unless a persisted :class:`Forecast` row with
``decision=approved`` covers the brief. A missing or pending forecast
triggers a fresh estimate (persisted as ``pending``) and raises
:class:`CostForecastApprovalRequiredError` so the operator can decide
via the API + dashboard.

The gate keeps the entry adapters thin: callers swap their direct
``work_pipeline.run(work_item)`` for ``forecast_gate.dispatch(work_item)``
and inherit the forecast workflow without learning about the
underlying ``CostForecaster`` / ``CostForecastRepository`` machinery.
"""

from typing import TYPE_CHECKING

from synthorg.budget.errors import (
    CostForecastApprovalRequiredError,
    CostForecastRejectedError,
)
from synthorg.budget.forecast_models import Forecast, ForecastDecision
from synthorg.budget.forecaster import BriefSignal
from synthorg.observability import get_logger
from synthorg.observability.events.budget import (
    BUDGET_FORECAST_APPROVAL_REQUIRED,
    BUDGET_FORECAST_REJECTED,
)

if TYPE_CHECKING:
    from synthorg.budget.config import BudgetConfig
    from synthorg.budget.forecaster import CostForecaster
    from synthorg.core.types import NotBlankStr
    from synthorg.engine.pipeline.models import WorkItem, WorkPipelineResult
    from synthorg.engine.pipeline.protocol import WorkPipeline
    from synthorg.persistence.cost_forecast_protocol import CostForecastRepository

logger = get_logger(__name__)


def _signal_from_work_item(
    work_item: WorkItem,
    *,
    currency: NotBlankStr,
) -> BriefSignal:
    """Build a :class:`BriefSignal` from a :class:`WorkItem`.

    At the work-entry stage the role-skeleton is not yet assigned;
    the gate uses a single-role placeholder (``"default"``) so the
    forecast is a coarse estimate over the brief text. A sharper
    estimate can be computed downstream once the work pipeline
    assigns concrete agents to roles.
    """
    return BriefSignal(
        brief_text=work_item.raw_intent,
        role_skeleton=("default",),
        model_assignments={},
        currency=currency,
    )


class ForecastGate:
    """Pre-flight cost forecast gate over a :class:`WorkPipeline`.

    Args:
        work_pipeline: The downstream pipeline the gate guards.
        forecaster: Service that produces fresh cost estimates.
        forecast_repo: Durable store for forecast rows.
        budget_config: Live budget configuration (drives the
            ``forecast_required`` toggle and stamps the currency on
            generated forecasts).
    """

    __slots__ = (
        "_budget_config",
        "_forecast_repo",
        "_forecaster",
        "_work_pipeline",
    )

    def __init__(
        self,
        *,
        work_pipeline: WorkPipeline,
        forecaster: CostForecaster,
        forecast_repo: CostForecastRepository,
        budget_config: BudgetConfig,
    ) -> None:
        self._work_pipeline = work_pipeline
        self._forecaster = forecaster
        self._forecast_repo = forecast_repo
        self._budget_config = budget_config

    async def dispatch(self, work_item: WorkItem) -> WorkPipelineResult:
        """Gate-check ``work_item`` and forward to the pipeline.

        Branching:

        * ``budget.forecast_required=False`` short-circuits: dispatch
          immediately without consulting the forecaster.
        * ``work_item.forecast_id`` points at an ``APPROVED`` row:
          dispatch.
        * ``work_item.forecast_id`` points at a ``REJECTED`` row:
          raise :class:`CostForecastRejectedError`.
        * Any other state (missing, pending, superseded): generate a
          fresh forecast, persist it, and raise
          :class:`CostForecastApprovalRequiredError` with the new
          forecast id so the dashboard can show the estimate.

        Raises:
            CostForecastApprovalRequiredError: When operator approval
                is required before dispatch.
            CostForecastRejectedError: When the linked forecast was
                explicitly rejected.
        """
        if not self._budget_config.forecast_required:
            return await self._work_pipeline.run(work_item)

        existing = await self._lookup_forecast(work_item)
        if existing is not None:
            if existing.decision is ForecastDecision.APPROVED:
                return await self._work_pipeline.run(work_item)
            if existing.decision is ForecastDecision.REJECTED:
                self._log_rejected(existing)
                msg = (
                    f"Cost forecast {existing.forecast_id!s} was "
                    f"rejected by the operator"
                )
                raise CostForecastRejectedError(
                    msg,
                    forecast_id=existing.forecast_id,
                    brief_hash=existing.brief_hash,
                )

        fresh = await self._issue_fresh_forecast(work_item)
        self._log_approval_required(fresh)
        msg = (
            f"Pre-flight cost forecast required: "
            f"estimated {fresh.estimated_cost:.4f} {fresh.currency} "
            f"awaiting operator approval"
        )
        raise CostForecastApprovalRequiredError(
            msg,
            forecast_id=fresh.forecast_id,
            brief_hash=fresh.brief_hash,
            estimated_cost=fresh.estimated_cost,
            currency=fresh.currency,
        )

    async def _lookup_forecast(self, work_item: WorkItem) -> Forecast | None:
        """Look up a forecast row by ``work_item.forecast_id``."""
        if work_item.forecast_id is None:
            return None
        return await self._forecast_repo.get(work_item.forecast_id)

    async def _issue_fresh_forecast(self, work_item: WorkItem) -> Forecast:
        """Generate and persist a fresh ``pending`` forecast."""
        signal = _signal_from_work_item(
            work_item,
            currency=self._budget_config.currency,
        )
        forecast = await self._forecaster.forecast(signal)
        await self._forecast_repo.save(forecast)
        return forecast

    def _log_rejected(self, forecast: Forecast) -> None:
        logger.warning(
            BUDGET_FORECAST_REJECTED,
            forecast_id=str(forecast.forecast_id),
            brief_hash=forecast.brief_hash,
        )

    def _log_approval_required(self, forecast: Forecast) -> None:
        logger.info(
            BUDGET_FORECAST_APPROVAL_REQUIRED,
            forecast_id=str(forecast.forecast_id),
            brief_hash=forecast.brief_hash,
            estimated_cost=forecast.estimated_cost,
            lower_bound=forecast.lower_bound,
            upper_bound=forecast.upper_bound,
            currency=forecast.currency,
        )


__all__ = ["ForecastGate"]
