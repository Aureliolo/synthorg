# module-kind: orchestrator
"""Startup wiring + adapter for the human plan-approval gate.

Attaches a plan-review gate to the work pipeline when
``coordination.plan_approval_required`` is set: splittable team work is then
parked for human approval before any team builds. The decomposed plan is
persisted as a durable :class:`~synthorg.core.plan.Plan` and the parked
approval carries only its ``plan_id``; on approval the plan is loaded and
rebuilt into a dispatch tree, so an operator's edits between parking and
approval are exactly what builds. Default off, so behaviour is unchanged
unless an operator opts in.
"""

import uuid
from typing import Final
from uuid import UUID

from synthorg.api.state import AppState
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalSource, ApprovalStatus
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.approval.state import approval_store_of
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.approval import ApprovalItem
from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.plan_review import PlanReview
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.models import DecompositionResult
from synthorg.engine.decomposition.plan_mapping import (
    PlanProvenance,
    plan_from_decomposition,
)
from synthorg.engine.pipeline.models import PlanReviewHandoff, WorkItem
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.persistence.plan_protocol import PlanRepository
from synthorg.providers.registry import ProviderRegistry

logger = get_logger(__name__)

_PLAN_ACTION_TYPE = "plan:approve"

#: ``ApprovalItem.metadata`` keys carrying resume context. The plan itself is
#: durable (referenced by ``plan_id``); the approval only points at it.
PROJECT_METADATA_KEY = "project"
PLAN_ID_METADATA_KEY = "plan_id"

_PREVIEW_SUBTASKS: Final[int] = 3

# Plan-approval risk scales with plan size: a larger plan commits more work and
# budget in one decision, so it warrants proportionally more scrutiny. (Risk
# level is otherwise a mostly-decorative label; scaling it with size at least
# makes it an honest signal here rather than a hardcoded constant.)
_LOW_RISK_MAX_SUBTASKS: Final[int] = 3
_MEDIUM_RISK_MAX_SUBTASKS: Final[int] = 8


def _plan_risk_level(plan: DecompositionResult) -> ApprovalRiskLevel:
    """Scale plan-approval risk with the size of the decomposed plan.

    Returns:
        ``LOW`` for a small plan, ``MEDIUM`` for a mid-sized one, ``HIGH``
        for a large plan (more subtasks commit more work in one approval).
    """
    count = len(plan.plan.subtasks)
    if count <= _LOW_RISK_MAX_SUBTASKS:
        return ApprovalRiskLevel.LOW
    if count <= _MEDIUM_RISK_MAX_SUBTASKS:
        return ApprovalRiskLevel.MEDIUM
    return ApprovalRiskLevel.HIGH


def _plan_detail(plan: DecompositionResult) -> str:
    """Human-readable one-line summary of a decomposed plan.

    Returns:
        A ``"<n> subtask(s): title, title, ..."`` summary.
    """
    subtasks = plan.plan.subtasks
    titles = ", ".join(s.title for s in subtasks[:_PREVIEW_SUBTASKS])
    suffix = ", ..." if len(subtasks) > _PREVIEW_SUBTASKS else ""
    head = f"{len(subtasks)} subtask(s)"
    return f"{head}: {titles}{suffix}" if titles else f"{head} awaiting approval"


class PlanReviewApprovalGate:
    """Parks a decomposed plan as an approval item before a team builds.

    Structurally satisfies the engine's ``PlanReviewGate`` port; wired onto
    the work pipeline by the startup hook so the engine never imports the
    approval store. The plan is persisted durably and the parked approval
    carries only its ``plan_id``; on approval the plan is reloaded and rebuilt
    into a dispatch tree, so an operator's edits are what actually build.
    """

    __slots__ = ("_approval_store", "_clock", "_plans")

    def __init__(
        self,
        *,
        approval_store: ApprovalStoreProtocol,
        plans: PlanRepository,
        clock: Clock,
    ) -> None:
        self._approval_store = approval_store
        self._plans = plans
        self._clock = clock

    async def request_plan_approval(
        self,
        *,
        work_item: WorkItem,
        task: Task,
        plan: DecompositionResult,
        review: PlanReview | None = None,
    ) -> PlanReviewHandoff:
        """Persist *plan* durably and park it as an approval item.

        The plan is persisted first so the parked approval always references
        a durable :class:`~synthorg.core.plan.Plan`; a persistence failure
        surfaces before any dangling approval is created.

        Returns:
            A :class:`PlanReviewHandoff` naming the parked approval item.
        """
        approval_id = uuid.uuid4()
        detail = _plan_detail(plan)
        now = self._clock.now()
        durable_plan = plan_from_decomposition(
            plan,
            PlanProvenance(
                project=work_item.project,
                objective_id=work_item.correlation_id,
                objective_title=NotBlankStr(task.title),
                parent_task_id=NotBlankStr(str(task.id)),
                created_at=now,
                forecast_id=work_item.forecast_id,
                review=review,
                objective_criteria=tuple(
                    NotBlankStr(c.description) for c in task.acceptance_criteria
                ),
            ),
        )
        await self._plans.create(durable_plan)
        approval = ApprovalItem(
            id=approval_id,
            action_type=NotBlankStr(_PLAN_ACTION_TYPE),
            title=NotBlankStr(f"Approve plan for: {task.title}"),
            description=NotBlankStr(detail),
            requested_by=work_item.requested_by,
            risk_level=_plan_risk_level(plan),
            source=ApprovalSource.PLAN_REVIEW,
            status=ApprovalStatus.PENDING,
            created_at=now,
            task_id=NotBlankStr(str(task.id)),
            metadata={
                PLAN_ID_METADATA_KEY: str(durable_plan.id),
                PROJECT_METADATA_KEY: work_item.project,
            },
        )
        try:
            await self._approval_store.add(approval)
        except Exception as exc:
            reraise_critical(exc)
            # The plan committed but the approval did not: without the
            # approval there is no route to ever approve or reject this plan,
            # so the row would be a permanent orphan (a retry mints a fresh
            # plan id, never colliding). Compensate by deleting the plan so
            # the failure surfaces cleanly and leaves no dangling record.
            await self._delete_orphan_plan(durable_plan.id)
            raise
        return PlanReviewHandoff(
            approval_id=NotBlankStr(str(approval_id)),
            subtask_count=len(plan.plan.subtasks),
            detail=NotBlankStr(detail),
        )

    async def _delete_orphan_plan(self, plan_id: UUID) -> None:
        """Best-effort delete of a plan orphaned by an approval-write failure.

        A secondary failure here is logged, not raised, so it never masks the
        original approval-store error the caller is propagating.
        """
        try:
            await self._plans.delete(NotBlankStr(str(plan_id)))
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                API_APP_STARTUP,
                service="plan_review_gate",
                note="failed to delete orphaned plan after approval-write failure",
                plan_id=str(plan_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )


async def wire_plan_review_gate(app_state: AppState) -> None:
    """Attach the plan-approval gate when the setting requires it.

    Best-effort + opt-in: a no-op unless ``coordination.plan_approval_required``
    is set and the work pipeline is wired. Default off keeps the historic
    dispatch-straight-to-team behaviour, so wiring this never changes an
    org that has not opted in.
    """
    from synthorg.approval.state import ApprovalStateSlice  # noqa: PLC0415
    from synthorg.engine.state import (  # noqa: PLC0415
        EngineStateSlice,
        work_pipeline_of,
    )
    from synthorg.persistence.state import PersistenceStateSlice  # noqa: PLC0415
    from synthorg.settings.state import config_resolver_of  # noqa: PLC0415

    if app_state.slice(EngineStateSlice).work_pipeline is None:
        return
    required = await config_resolver_of(app_state).get_bool(
        "coordination", "plan_approval_required"
    )
    if not required:
        return
    # Best-effort: the approval store + persistence backend are normally wired
    # by the time this hook runs, but an early boot (before persistence
    # connects) can reach here without them. Skip rather than let an accessor
    # raise a 503 out of a wiring hook. The operator explicitly opted into a
    # mandatory gate, so a skip is warn-worthy: without it every splittable
    # plan builds ungated, and a silent skip would hide that regression.
    if app_state.slice(ApprovalStateSlice).store is None:
        logger.warning(
            API_APP_STARTUP,
            service="plan_review_gate",
            note="skipped: plan_approval_required but approval store not wired",
        )
        return
    backend = app_state.slice(PersistenceStateSlice).backend
    if backend is None:
        logger.warning(
            API_APP_STARTUP,
            service="plan_review_gate",
            note="skipped: plan_approval_required but persistence not wired",
        )
        return
    gate = PlanReviewApprovalGate(
        approval_store=approval_store_of(app_state),
        plans=backend.plans,
        clock=app_state.clock,
    )
    work_pipeline_of(app_state).attach_plan_review_gate(gate)
    logger.info(API_APP_STARTUP, service="plan_review_gate", note="wired")


async def wire_plan_review_panel(
    app_state: AppState,
    *,
    provider_registry: ProviderRegistry | None,
    cost_tracker: CostTrackerProtocol | None,
) -> None:
    """Attach the stakeholder plan-review panel when enabled and a provider exists.

    Best-effort + opt-out: the panel is on by default but only meaningful when
    plan approval is gated (it runs inside the gated-plan flow) and a provider
    serves the decomposition model. It reviews the same plans the decomposer
    builds, so it reuses ``coordination.decomposition_model`` rather than
    introducing a second required model setting. An absent provider or a
    disabled setting leaves the pipeline panel-less (a gated plan is parked for
    approval with no panel review), so wiring this never blocks a boot.
    """
    from synthorg.engine.plan_review.models import (  # noqa: PLC0415
        PlanReviewPanelConfig,
    )
    from synthorg.engine.plan_review.session import (  # noqa: PLC0415
        AgentSessionPlanReviewPanel,
    )
    from synthorg.engine.state import (  # noqa: PLC0415
        EngineStateSlice,
        work_pipeline_of,
    )
    from synthorg.settings.state import config_resolver_of  # noqa: PLC0415

    if app_state.slice(EngineStateSlice).work_pipeline is None:
        return
    resolver = config_resolver_of(app_state)
    if not await resolver.get_bool("coordination", "plan_review_panel_enabled"):
        return
    if provider_registry is None:
        return
    from synthorg.api._feature_provider_resolution import (  # noqa: PLC0415
        resolve_feature_provider,
    )

    model = await resolver.get_str("coordination", "decomposition_model")
    provider = resolve_feature_provider(
        provider_registry, model, feature="plan_review_panel"
    )
    if provider is None:
        return
    config = PlanReviewPanelConfig(
        panel_size=await resolver.get_int("coordination", "plan_review_panel_size"),
        max_turns=await resolver.get_int("coordination", "plan_review_panel_max_turns"),
        cost_ceiling=await resolver.get_float(
            "coordination", "plan_review_panel_cost_ceiling"
        ),
    )
    panel = AgentSessionPlanReviewPanel(
        provider=provider,
        config=config,
        cost_tracker=cost_tracker,
        clock=app_state.clock,
    )
    work_pipeline_of(app_state).attach_plan_review_panel(panel)
    logger.info(API_APP_STARTUP, service="plan_review_panel", note="wired")
