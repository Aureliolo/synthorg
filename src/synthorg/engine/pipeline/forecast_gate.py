# module-kind: service
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

from typing import TYPE_CHECKING, NoReturn

from synthorg.budget.errors import (
    CostForecastApprovalRequiredError,
    CostForecastRejectedError,
)
from synthorg.budget.forecast_models import Forecast, ForecastDecision
from synthorg.budget.forecaster import BriefSignal, compute_brief_hash
from synthorg.core.persistence_errors import ConstraintViolationError
from synthorg.observability import get_logger
from synthorg.observability.events.budget import (
    BUDGET_FORECAST_APPROVAL_REQUIRED,
    BUDGET_FORECAST_REJECTED,
    BUDGET_FORECAST_SUPERSEDED,
)
from synthorg.persistence.cost_forecast_protocol import CostForecastFilterSpec

if TYPE_CHECKING:
    from synthorg.budget.config import BudgetConfig
    from synthorg.budget.forecaster import CostForecaster
    from synthorg.core.types import NotBlankStr
    from synthorg.engine.pipeline.models import WorkItem, WorkPipelineResult
    from synthorg.engine.pipeline.narrator_port import RunNarrator
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

    Returns:
        A :class:`BriefSignal` carrying the work item's raw intent,
        the placeholder role skeleton, and the configured currency.
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

    async def run(self, work_item: WorkItem) -> WorkPipelineResult:
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

        Returns:
            The :class:`WorkPipelineResult` produced by the wrapped
            pipeline when the gate permits dispatch.

        Raises:
            CostForecastApprovalRequiredError: When operator approval
                is required before dispatch.
            CostForecastRejectedError: When the linked forecast was
                explicitly rejected.
        """
        if not self._budget_config.forecast_required:
            return await self._work_pipeline.run(work_item)
        return await self._gated_dispatch(work_item)

    def attach_narrator(self, narrator: RunNarrator) -> None:
        """Forward the narrator to the wrapped pipeline (decorator passthrough)."""
        self._work_pipeline.attach_narrator(narrator)

    async def _gated_dispatch(self, work_item: WorkItem) -> WorkPipelineResult:
        """Run the forecast-gated dispatch branches.

        Returns:
            The :class:`WorkPipelineResult` when the gate permits dispatch.

        Raises:
            CostForecastApprovalRequiredError: When operator approval is
                required before dispatch.
            CostForecastRejectedError: When the linked forecast was
                explicitly rejected.
        """
        existing = await self._lookup_forecast(work_item)
        if existing is not None and self._forecast_covers_brief(work_item, existing):
            if existing.decision is ForecastDecision.APPROVED:
                # Carry the operator-approved ceiling onto the work item so
                # the intake phase can stamp it onto the Task; without this
                # the in-loop BudgetChecker only sees the global fallback.
                released = work_item.model_copy(
                    update={"hard_ceiling": existing.ceiling_amount},
                )
                return await self._work_pipeline.run(released)
            if existing.decision is ForecastDecision.REJECTED:
                self._raise_rejected(existing)
            if existing.decision is ForecastDecision.PENDING:
                self._raise_approval_required(existing)

        # No matching forecast via the caller's id: reuse a pending row for
        # this brief if one exists, else mint one.
        forecast = await self._forecast_for_brief(work_item)
        # Inlined (rather than via _raise_approval_required) so the static
        # analyser sees run() always terminates in a return or raise.
        self._log_approval_required(forecast)
        msg = (
            f"Pre-flight cost forecast required: "
            f"estimated {forecast.estimated_cost:.4f} {forecast.currency} "
            f"awaiting operator approval"
        )
        raise CostForecastApprovalRequiredError(
            msg,
            forecast_id=forecast.forecast_id,
            brief_hash=forecast.brief_hash,
            estimated_cost=forecast.estimated_cost,
            currency=forecast.currency,
        )

    async def _forecast_for_brief(self, work_item: WorkItem) -> Forecast:
        """Return the pending forecast for the brief, minting one if absent.

        Reuses an existing pending row (the partial-unique index permits
        only one per brief). On a save race where a concurrent dispatch
        wins that index, re-reads the winner rather than surfacing the
        constraint violation.

        Returns:
            The pending :class:`Forecast` for the brief: an existing
            one when present, a freshly persisted one otherwise (or
            the winning row when a save race occurred).

        Raises:
            ConstraintViolationError: When a save race occurs but the
                winner cannot be re-read (the original error is
                re-raised so the operator sees the constraint hit).
        """
        signal = _signal_from_work_item(
            work_item,
            currency=self._budget_config.currency,
        )
        brief_hash = compute_brief_hash(signal)
        pending = await self._pending_forecast_for_brief(brief_hash)
        if pending is not None:
            return pending
        fresh = await self._forecaster.forecast(signal)
        try:
            await self._forecast_repo.save(fresh)
        except ConstraintViolationError:
            raced = await self._pending_forecast_for_brief(brief_hash)
            if raced is None:
                raise
            return raced
        return fresh

    def _raise_approval_required(self, forecast: Forecast) -> NoReturn:
        """Log and raise the approval-required signal for a pending forecast.

        Raises:
            CostForecastApprovalRequiredError: Always; the function
                exists to centralise the log + raise pair.
        """
        self._log_approval_required(forecast)
        msg = (
            f"Pre-flight cost forecast required: "
            f"estimated {forecast.estimated_cost:.4f} {forecast.currency} "
            f"awaiting operator approval"
        )
        raise CostForecastApprovalRequiredError(
            msg,
            forecast_id=forecast.forecast_id,
            brief_hash=forecast.brief_hash,
            estimated_cost=forecast.estimated_cost,
            currency=forecast.currency,
        )

    def _raise_rejected(self, forecast: Forecast) -> NoReturn:
        """Log and raise the rejected signal for a rejected forecast.

        Raises:
            CostForecastRejectedError: Always; the function exists to
                centralise the log + raise pair.
        """
        self._log_rejected(forecast)
        msg = f"Cost forecast {forecast.forecast_id!s} was rejected by the operator"
        raise CostForecastRejectedError(
            msg,
            forecast_id=forecast.forecast_id,
            brief_hash=forecast.brief_hash,
        )

    async def _lookup_forecast(self, work_item: WorkItem) -> Forecast | None:
        """Look up a forecast row by ``work_item.forecast_id``.

        Returns:
            The :class:`Forecast` referenced by ``work_item.forecast_id``;
            ``None`` when no id is set or no row matches.
        """
        if work_item.forecast_id is None:
            return None
        return await self._forecast_repo.get(work_item.forecast_id)

    async def _pending_forecast_for_brief(
        self,
        brief_hash: str,
    ) -> Forecast | None:
        """Return the existing pending forecast for ``brief_hash``, if any.

        The repository's partial-unique index allows at most one pending
        row per brief, so a hit here is the single reusable forecast.

        Returns:
            The pending :class:`Forecast` for ``brief_hash`` when one
            exists; ``None`` otherwise.
        """
        rows = await self._forecast_repo.query(
            CostForecastFilterSpec(
                brief_hash=brief_hash,
                decision=ForecastDecision.PENDING,
            ),
            limit=1,
        )
        return rows[0] if rows else None

    def _forecast_covers_brief(
        self,
        work_item: WorkItem,
        existing: Forecast,
    ) -> bool:
        """Reject a forecast whose brief no longer matches the work item.

        A reused ``forecast_id`` pointing at a row for a *different*
        brief (the operator edited the brief after the forecast was
        issued) must not carry its stale approval / rejection / ceiling
        onto the new brief. On a mismatch the gate falls through to
        issue a fresh forecast for the current brief.

        Returns:
            ``True`` when the existing forecast's brief hash matches
            the current work item; ``False`` when the brief has
            drifted (a fresh forecast is required).
        """
        expected = compute_brief_hash(
            _signal_from_work_item(
                work_item,
                currency=self._budget_config.currency,
            ),
        )
        if existing.brief_hash == expected:
            return True
        logger.warning(
            BUDGET_FORECAST_SUPERSEDED,
            forecast_id=str(existing.forecast_id),
            stored_brief_hash=existing.brief_hash,
            work_item_brief_hash=expected,
        )
        return False

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
