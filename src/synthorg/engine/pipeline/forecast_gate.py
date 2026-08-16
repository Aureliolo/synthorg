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

from typing import NoReturn

from synthorg.budget.config import BudgetConfig
from synthorg.budget.errors import (
    CostForecastApprovalRequiredError,
    CostForecastRejectedError,
)
from synthorg.budget.forecast_models import Forecast, ForecastDecision
from synthorg.budget.forecast_roles import (
    DEFAULT_ROLE_SKELETON,
    BriefRoleSkeleton,
    RoleSkeletonProvider,
)
from synthorg.budget.forecaster import BriefSignal, CostForecaster, compute_brief_hash
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.persistence_errors import ConstraintViolationError
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.pipeline.charter_authority_port import CharterAuthority
from synthorg.engine.pipeline.models import (
    PipelineAttachments,
    WorkItem,
    WorkPipelineResult,
)
from synthorg.engine.pipeline.narrator_port import RunNarrator
from synthorg.engine.pipeline.plan_review_panel_port import PlanReviewPanel
from synthorg.engine.pipeline.plan_review_port import PlanReviewGate
from synthorg.engine.pipeline.protocol import WorkPipeline
from synthorg.engine.pipeline.refinement_port import WorkRefinementRouter
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.budget import (
    BUDGET_FORECAST_APPROVAL_REQUIRED,
    BUDGET_FORECAST_REJECTED,
    BUDGET_FORECAST_SUPERSEDED,
    BUDGET_PREFLIGHT_ERROR,
)
from synthorg.persistence.cost_forecast_protocol import (
    CostForecastFilterSpec,
    CostForecastRepository,
)

logger = get_logger(__name__)


def _signal_from_work_item(
    work_item: WorkItem,
    *,
    currency: NotBlankStr,
    skeleton: BriefRoleSkeleton,
) -> BriefSignal:
    """Build a :class:`BriefSignal` from a :class:`WorkItem` + role skeleton.

    The skeleton is resolved once per dispatch (from the live roster when a
    provider is wired, else the single-role default) so the forecast spans
    every role that could participate instead of a single placeholder.

    Returns:
        A :class:`BriefSignal` carrying the work item's raw intent, where it
        lands, who asked for it and which submission it is, the resolved role
        skeleton + per-role model assignments, and the currency.
    """
    return BriefSignal(
        brief_text=work_item.raw_intent,
        project=work_item.project,
        requested_by=work_item.requested_by,
        correlation_id=work_item.correlation_id,
        role_skeleton=skeleton.roles,
        model_assignments=skeleton.model_assignments,
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
        "_role_skeleton_provider",
        "_work_pipeline",
    )

    def __init__(
        self,
        *,
        work_pipeline: WorkPipeline,
        forecaster: CostForecaster,
        forecast_repo: CostForecastRepository,
        budget_config: BudgetConfig,
        role_skeleton_provider: RoleSkeletonProvider | None = None,
    ) -> None:
        self._work_pipeline = work_pipeline
        self._forecaster = forecaster
        self._forecast_repo = forecast_repo
        self._budget_config = budget_config
        self._role_skeleton_provider = role_skeleton_provider

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

    async def intake_only(self, work_item: WorkItem) -> Task:
        """Forward intake to the wrapped pipeline (decorator passthrough).

        The forecast gate guards the batch ``run`` entry seam. The
        intake/continue split is driven only by the conversational-propose
        path, whose cost forecast is resolved upstream at proposal time, so
        the gate forwards the split verbatim without re-consulting a forecast.

        Returns:
            The task created by the wrapped pipeline's intake.
        """
        return await self._work_pipeline.intake_only(work_item)

    async def continue_from_intake(
        self, work_item: WorkItem, task: Task
    ) -> WorkPipelineResult:
        """Forward the post-intake spine to the wrapped pipeline (passthrough).

        Returns:
            The terminal :class:`WorkPipelineResult` from the wrapped pipeline.
        """
        return await self._work_pipeline.continue_from_intake(work_item, task)

    def attach_charter_authority(self, authority: CharterAuthority | None) -> None:
        """Forward the charter authority to the wrapped pipeline (passthrough)."""
        self._work_pipeline.attach_charter_authority(authority)

    def attach_narrator(self, narrator: RunNarrator | None) -> None:
        """Forward the narrator to the wrapped pipeline (decorator passthrough)."""
        self._work_pipeline.attach_narrator(narrator)

    def attach_refinement_router(self, router: WorkRefinementRouter | None) -> None:
        """Forward the refinement router to the wrapped pipeline (passthrough)."""
        self._work_pipeline.attach_refinement_router(router)

    def attach_plan_review_gate(self, gate: PlanReviewGate) -> None:
        """Forward the plan-review gate to the wrapped pipeline (passthrough)."""
        self._work_pipeline.attach_plan_review_gate(gate)

    def attach_plan_review_panel(self, panel: PlanReviewPanel | None) -> None:
        """Forward the plan-review panel to the wrapped pipeline (passthrough)."""
        self._work_pipeline.attach_plan_review_panel(panel)

    @property
    def attachments(self) -> PipelineAttachments:
        """Report the wrapped pipeline's attachments (decorator passthrough).

        Returns:
            The wrapped pipeline's :class:`PipelineAttachments`.
        """
        return self._work_pipeline.attachments

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
        skeleton = await self._resolve_skeleton()
        signal = _signal_from_work_item(
            work_item,
            currency=self._budget_config.currency,
            skeleton=skeleton,
        )
        brief_hash = compute_brief_hash(signal)
        existing = await self._lookup_forecast(work_item)
        if existing is not None and self._forecast_covers_brief(
            signal, brief_hash, existing
        ):
            # Claiming binds the row to this submission before anything is
            # spent under it. An estimate raised on its own belongs to
            # whoever reaches it first: without the claim a second caller
            # naming the same id would run a second time under one approved
            # ceiling, or be told to await an approval that releases the
            # first caller's work and never its own.
            claimed = await self._claim_for_submission(
                existing, work_item, brief_hash=brief_hash
            )
            if claimed is None:
                # Another submission took the estimate between the read and
                # the claim. This one is not covered by it, so it falls
                # through to a forecast of its own.
                return await self._mint_and_refuse(work_item, signal, brief_hash)
            if claimed.decision is ForecastDecision.APPROVED:
                # Carry the operator-approved ceiling onto the work item so
                # the intake phase can stamp it onto the Task; without this
                # the in-loop BudgetChecker only sees the global fallback.
                released = work_item.model_copy(
                    update={"hard_ceiling": claimed.ceiling_amount},
                )
                return await self._work_pipeline.run(released)
            if claimed.decision is ForecastDecision.REJECTED:
                self._raise_rejected(claimed)
            if claimed.decision is ForecastDecision.PENDING:
                self._raise_approval_required(claimed)

        # No matching forecast via the caller's id: reuse a pending row for
        # this brief if one exists, else mint one.
        return await self._mint_and_refuse(work_item, signal, brief_hash)

    async def _mint_and_refuse(
        self, work_item: WorkItem, signal: BriefSignal, brief_hash: str
    ) -> NoReturn:
        """Give the submission its own pending forecast and refuse dispatch.

        Raises:
            CostForecastApprovalRequiredError: Always; the submission has
                a forecast of its own now and the operator decides it.
        """
        forecast = await self._forecast_for_brief(work_item, signal, brief_hash)
        self._raise_approval_required(forecast)

    async def _resolve_skeleton(self) -> BriefRoleSkeleton:
        """Resolve the role skeleton for this dispatch, once.

        Returns:
            The live roster's role skeleton when a provider is wired and yields
            roles; the single-role default otherwise.
        """
        if self._role_skeleton_provider is None:
            return DEFAULT_ROLE_SKELETON
        try:
            skeleton = await self._role_skeleton_provider()
        except Exception as exc:
            reraise_critical(exc)
            logger.error(
                BUDGET_PREFLIGHT_ERROR,
                reason="role_skeleton_provider_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        return skeleton if skeleton.roles else DEFAULT_ROLE_SKELETON

    async def _forecast_for_brief(
        self, work_item: WorkItem, signal: BriefSignal, brief_hash: str
    ) -> Forecast:
        """Return the pending forecast for the brief, minting one if absent.

        Reuses an existing pending row (the partial-unique index permits
        only one per digest). The digest identifies the submission, so a
        hit is this work item's own row rather than another submission's
        that happened to share the brief text. A hit that carries no work
        item gates nothing, so this attaches one before returning it; that
        is the only shape a row minted by an estimate-only request or an
        earlier build can have. On a save race where a concurrent dispatch
        wins the index, re-reads the winner rather than surfacing the
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
        pending = await self._pending_forecast_for_brief(brief_hash)
        if pending is not None:
            claimed = await self._claim_for_submission(
                pending, work_item, brief_hash=brief_hash
            )
            if claimed is not None:
                return claimed
        estimated = await self._forecaster.forecast(signal)
        # The work item rides along with the estimate it blocked, so approving
        # the forecast can run the work. Without it the caller's 202 is a lie:
        # the door accepted the brief, then dropped it with nothing left to
        # re-dispatch from.
        fresh = estimated.model_copy(
            update={"gated_work_item": work_item.model_dump(mode="json")},
        )
        try:
            await self._forecast_repo.save(fresh)
        except ConstraintViolationError:
            raced = await self._pending_forecast_for_brief(brief_hash)
            if raced is None:
                raise
            claimed = await self._claim_for_submission(
                raced, work_item, brief_hash=brief_hash
            )
            if claimed is None:
                # The winner's row is spoken for and its digest moved with
                # it, so this digest is free again and the estimate this
                # submission already paid for is the one to persist.
                await self._forecast_repo.save(fresh)
                return fresh
            return claimed
        return fresh

    async def _claim_for_submission(
        self, existing: Forecast, work_item: WorkItem, *, brief_hash: str
    ) -> Forecast | None:
        """Bind an unclaimed forecast to the submission now carrying it.

        A row that gates nothing cannot be redispatched on approval, so
        returning one unchanged would hand the caller a 202 for work the
        approval can never run. Claiming re-keys the digest at the same
        time: an estimate raised on its own was keyed before any
        submission existed, and leaving it that way would let the next
        caller of the same brief reuse a row that is already spoken for.
        Once claimed the row answers only to its own submission.

        The claim is a conditional write rather than a read then a save,
        because what a claimed estimate carries is an approved ceiling:
        two submissions that each read the row free and each wrote would
        both dispatch, spending one operator approval twice.

        Returns:
            The row, carrying the work item it gates; unchanged when this
            submission already holds it; ``None`` when another submission
            claimed it first, so this one needs a forecast of its own.
        """
        if existing.gated_work_item is not None:
            return existing
        held = work_item.model_dump(mode="json")
        won = await self._forecast_repo.claim_if_unclaimed(
            existing.forecast_id,
            gated_work_item=held,
            brief_hash=NotBlankStr(brief_hash),
            updated_at=existing.updated_at,
        )
        if not won:
            logger.warning(
                BUDGET_FORECAST_SUPERSEDED,
                forecast_id=str(existing.forecast_id),
                stored_brief_hash=existing.brief_hash,
                work_item_brief_hash=brief_hash,
                reason="claimed_by_another_submission",
            )
            return None
        return existing.model_copy(
            update={"gated_work_item": held, "brief_hash": NotBlankStr(brief_hash)},
        )

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
        signal: BriefSignal,
        expected: str,
        existing: Forecast,
    ) -> bool:
        """Reject a forecast whose brief no longer matches the work item.

        A reused ``forecast_id`` pointing at a row for a *different*
        brief (the operator edited the brief after the forecast was
        issued) must not carry its stale approval / rejection / ceiling
        onto the new brief. On a mismatch the gate falls through to
        issue a fresh forecast for the current brief.

        Two digests are accepted, because a stored row can have been keyed
        either way. A row this gate minted is keyed to the submission, so
        it matches the submission-scoped digest whoever is carrying it. A
        row generated on its own through ``POST /budget/forecast`` was
        keyed before any submission existed, so it matches the brief-only
        one -- but only while it is still free: once it gates a work item
        it belongs to that submission, and a second submission naming the
        same id would otherwise be told to await an approval that releases
        the first one's work and never its own.

        Returns:
            ``True`` when the existing forecast is for this brief;
            ``False`` when the brief has drifted (a fresh forecast is
            required).
        """
        if existing.brief_hash == expected:
            return True
        brief_only = compute_brief_hash(
            signal.model_copy(update={"correlation_id": None})
        )
        if existing.gated_work_item is None and existing.brief_hash == brief_only:
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
