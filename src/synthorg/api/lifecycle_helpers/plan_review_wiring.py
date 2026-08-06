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
from datetime import datetime
from typing import Final
from uuid import UUID

from synthorg.api.channels import PlanNotifier
from synthorg.api.state import AppState
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalSource, ApprovalStatus
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.approval.state import approval_store_of
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.approval import ApprovalItem
from synthorg.core.clock import Clock
from synthorg.core.concurrency import CASRetryHandler
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.domain_errors import (
    PlanParentTaskMissingError,
    ResourceNotFoundError,
    VersionConflictError,
)
from synthorg.core.persistence_errors import PersistenceVersionConflictError
from synthorg.core.plan import Plan
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.plan_review import PlanReview
from synthorg.core.task import Task
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.models import DecompositionResult
from synthorg.engine.decomposition.plan_mapping import (
    PlanProvenance,
    plan_from_decomposition,
    plan_shell,
)
from synthorg.engine.pipeline.models import PlanReviewHandoff, WorkItem
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.api import API_APP_STARTUP
from synthorg.observability.events.pipeline import (
    PIPELINE_PLAN_APPROVAL_PARK_FAILED,
    PIPELINE_PLAN_FAIL_SHELL_MISSING,
    PIPELINE_PLAN_FAIL_WRITE_FAILED,
    PIPELINE_PLAN_MARKED_FAILED,
    PIPELINE_PLAN_PARENT_MISSING,
    PIPELINE_PLAN_SHELL_OPENED,
)
from synthorg.persistence.plan_protocol import PlanRepository
from synthorg.persistence.task_protocol import TaskRepository
from synthorg.providers.registry import ProviderRegistry

logger = get_logger(__name__)

_PLAN_ACTION_TYPE = "plan:approve"

# Bounded compare-and-swap retries when the plan is reworked concurrently with
# its FAILED-compensation write, so a losing CAS re-reads and reapplies rather
# than aborting the one write meant to make a failure visible.
_MAX_FAIL_ATTEMPTS: Final[int] = 3

#: ``ApprovalItem.metadata`` keys carrying resume context. The plan itself is
#: durable (referenced by ``plan_id``); the approval only points at it.
PROJECT_METADATA_KEY = "project"
PLAN_ID_METADATA_KEY = "plan_id"

_PREVIEW_SUBTASKS: Final[int] = 3

#: Wall-clock cap for one plan-item reply completion. A reply is a single
#: bounded call, so it shares the order of a normal agent call timeout.
_REPLY_TIMEOUT_SECONDS: Final[float] = 120.0

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

    __slots__ = ("_approval_store", "_clock", "_notifier", "_plans", "_tasks")

    def __init__(
        self,
        *,
        approval_store: ApprovalStoreProtocol,
        plans: PlanRepository,
        tasks: TaskRepository,
        clock: Clock,
        notifier: PlanNotifier | None = None,
    ) -> None:
        self._approval_store = approval_store
        self._plans = plans
        self._tasks = tasks
        self._clock = clock
        self._notifier = notifier

    def _announce(self, plan: Plan) -> None:
        """Tell open viewers the plan moved, if a publisher is wired.

        Every write here happens on a background spine, after the request that
        started it returned, so nothing else announces them: a page open during
        decomposition rendered the pre-decomposition snapshot next to a fresh
        approval prompt until it was reloaded by hand.

        Args:
            plan: The plan as it now stands.
        """
        if self._notifier is not None:
            self._notifier(plan)

    def _provenance(
        self,
        work_item: WorkItem,
        task: Task,
        now: datetime,
        *,
        status: PlanStatus,
        review: PlanReview | None,
    ) -> PlanProvenance:
        """Build the plan provenance shared by the shell and the filled plan.

        Returns:
            A :class:`PlanProvenance` stamping the objective / project identity,
            timing, lifecycle status, and (denormalised) review context.
        """
        return PlanProvenance(
            project=work_item.project,
            objective_id=work_item.correlation_id,
            objective_title=NotBlankStr(task.title),
            parent_task_id=NotBlankStr(str(task.id)),
            created_at=now,
            status=status,
            forecast_id=work_item.forecast_id,
            review=review,
            objective_criteria=tuple(
                NotBlankStr(c.description) for c in task.acceptance_criteria
            ),
        )

    async def _require_parent(self, task: Task, plan_id: UUID) -> None:
        """Refuse to park a plan whose objective task is gone.

        Decomposition runs for minutes, and a delete landing in that window
        used to be invisible: the run completed against the deleted row, the
        plan reached PENDING_REVIEW, and the operator was asked to approve
        nine items under a task that 404s. The foreign key now refuses the
        delete, but a task deleted before that constraint existed, or one
        removed by a path that bypasses the API, still has to be caught
        before the approval is parked rather than after.

        Raises:
            PlanParentTaskMissingError: When the task no longer exists.
        """
        if await self._tasks.get(NotBlankStr(str(task.id))) is not None:
            return
        logger.warning(
            PIPELINE_PLAN_PARENT_MISSING,
            plan_id=str(plan_id),
            task_id=str(task.id),
        )
        msg = (
            f"objective task {task.id} was deleted while its plan was being "
            "decomposed, so the plan has nothing to build under"
        )
        raise PlanParentTaskMissingError(msg)

    async def open_plan(self, *, work_item: WorkItem, task: Task) -> UUID:
        """Persist a PLANNING plan shell before decomposition runs.

        Returns:
            The id of the persisted PLANNING shell.
        """
        now = self._clock.now()
        shell = plan_shell(
            self._provenance(
                work_item, task, now, status=PlanStatus.PLANNING, review=None
            )
        )
        await self._plans.create(shell)
        logger.info(
            PIPELINE_PLAN_SHELL_OPENED,
            plan_id=str(shell.id),
            project=work_item.project,
            task_id=str(task.id),
        )
        return shell.id

    async def request_plan_approval(
        self,
        *,
        plan_id: UUID,
        work_item: WorkItem,
        task: Task,
        plan: DecompositionResult,
        review: PlanReview | None = None,
    ) -> PlanReviewHandoff:
        """Fill the PLANNING shell with *plan* and park it as an approval item.

        The shell (persisted by :meth:`open_plan`) is updated in place to
        PENDING_REVIEW with the decomposed items, so a plan is first-class from
        greenlight and the parked approval references the same durable id.

        Returns:
            A :class:`PlanReviewHandoff` naming the parked approval item.

        Raises:
            PlanParentTaskMissingError: When the objective task was deleted
                while decomposition ran. The caller compensates by failing
                the plan, so an orphan never reaches the review queue.
        """
        await self._require_parent(task, plan_id)
        approval_id = uuid.uuid4()
        detail = _plan_detail(plan)
        now = self._clock.now()
        shell = await self._plans.get(NotBlankStr(str(plan_id)))
        filled = plan_from_decomposition(
            plan,
            self._provenance(
                work_item, task, now, status=PlanStatus.PENDING_REVIEW, review=review
            ),
        )
        durable_plan = filled.model_copy(
            update={
                "id": plan_id,
                "created_at": shell.created_at if shell is not None else now,
                "version": (shell.version + 1) if shell is not None else 1,
                "updated_at": now,
            }
        )
        if shell is not None:
            await self._plans.update(durable_plan, expected_version=shell.version)
        else:
            # The shell was lost (e.g. opened on a prior boot then pruned);
            # persist the filled plan fresh so the approval still references a
            # durable plan rather than dangling.
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
            # The plan is filled but the approval did not park: without an
            # approval there is no route to approve or reject it, so mark the
            # durable plan FAILED (it stays visible in Plan Review, carrying the
            # reason) rather than leaving a PENDING_REVIEW plan with no approval.
            logger.warning(
                PIPELINE_PLAN_APPROVAL_PARK_FAILED,
                plan_id=str(durable_plan.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            await self.fail_plan(
                plan_id=durable_plan.id, reason="approval-store write failed"
            )
            raise
        self._announce(durable_plan)
        return PlanReviewHandoff(
            approval_id=NotBlankStr(str(approval_id)),
            plan_id=NotBlankStr(str(durable_plan.id)),
            subtask_count=len(plan.plan.subtasks),
            detail=NotBlankStr(detail),
        )

    async def fail_plan(self, *, plan_id: UUID, reason: str) -> None:
        """Mark a plan FAILED so a failed run leaves a visible plan, best-effort.

        This is the compensating write on every plan-review failure path, so it
        is hardened three ways: it is idempotent (a plan already FAILED, or a
        missing shell, is a no-op), a concurrent rework is retried via CAS, and a
        persistent write failure is logged, not raised. It must never mask the
        original failure the caller is surfacing (a re-raised approval error, or
        the failed handoff `_plan_review` returns) nor turn a handled failure
        into a 500 -- which is exactly what an unguarded write here would do.
        """
        key = NotBlankStr(str(plan_id))
        marked_reason = NotBlankStr(reason or "decomposition failed")

        async def read() -> tuple[Plan, int]:
            plan = await self._plans.get(key)
            if plan is None:
                msg = "plan shell missing"
                raise ResourceNotFoundError(msg)
            return plan, plan.version

        async def write(plan: Plan, _version: int) -> None:
            if plan.status is PlanStatus.FAILED:
                return  # idempotent: a prior compensation already marked it
            failed = plan.model_copy(
                update={
                    "status": PlanStatus.FAILED,
                    "failure_reason": marked_reason,
                    "version": plan.version + 1,
                    "updated_at": self._clock.now(),
                }
            )
            try:
                await self._plans.update(failed, expected_version=plan.version)
            except PersistenceVersionConflictError as exc:
                # Translate to the domain twin CASRetryHandler retries on.
                raise VersionConflictError(str(exc)) from exc
            # After the write, never before: a viewer told the plan failed and
            # then refetching a plan that is still PENDING_REVIEW would read as
            # the announcement being wrong rather than early.
            self._announce(failed)

        try:
            await CASRetryHandler(
                resource="plan_fail", max_attempts=_MAX_FAIL_ATTEMPTS
            ).execute(read, write)
        except ResourceNotFoundError:
            logger.warning(
                PIPELINE_PLAN_FAIL_SHELL_MISSING, plan_id=str(plan_id), reason=reason
            )
            return
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- the compensating FAILED write is a
            # best-effort side channel; the caller already surfaces the failure,
            # so a persistent write error (or exhausted CAS) must not mask it or
            # escape as a 500 from the greenlit run.
            reraise_critical(exc)
            logger.error(
                PIPELINE_PLAN_FAIL_WRITE_FAILED,
                plan_id=str(plan_id),
                reason=reason,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return
        logger.info(PIPELINE_PLAN_MARKED_FAILED, plan_id=str(plan_id), reason=reason)


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
    from synthorg.api.api_core_state import ApiCoreStateSlice  # noqa: PLC0415

    gate = PlanReviewApprovalGate(
        approval_store=approval_store_of(app_state),
        plans=backend.plans,
        tasks=backend.tasks,
        clock=app_state.clock,
        notifier=app_state.slice(ApiCoreStateSlice).plan_notifier,
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
    from synthorg.core.agent import AgentIdentity  # noqa: PLC0415
    from synthorg.providers.protocol import CompletionProvider  # noqa: PLC0415

    registry = provider_registry

    def _panel_provider_selector(identity: AgentIdentity) -> CompletionProvider:
        # Each panellist dispatches on its own bound provider; an unregistered
        # provider raises and the session degrades that panellist to no-verdict.
        return registry.get(identity.model.provider)

    config = PlanReviewPanelConfig(
        panel_size=await resolver.get_int("coordination", "plan_review_panel_size"),
        max_turns=await resolver.get_int("coordination", "plan_review_panel_max_turns"),
        cost_ceiling=await resolver.get_float(
            "coordination", "plan_review_panel_cost_ceiling"
        ),
    )
    panel = AgentSessionPlanReviewPanel(
        provider_selector=_panel_provider_selector,
        config=config,
        cost_tracker=cost_tracker,
        clock=app_state.clock,
    )
    work_pipeline_of(app_state).attach_plan_review_panel(panel)
    logger.info(API_APP_STARTUP, service="plan_review_panel", note="wired")


async def wire_plan_item_reply_service(
    app_state: AppState,
    *,
    provider_registry: ProviderRegistry | None,
    cost_tracker: CostTrackerProtocol | None,
) -> None:
    """Wire the conversational plan-item reply service when a model is set.

    Built unconditionally of ``plan_review_reply_enabled`` so the live
    per-comment gate can flip without a restart; returns without wiring only
    when no ``plan_review_reply_model`` is configured or no provider serves it,
    leaving plan comments unanswered. Best-effort: an absent provider registry
    or an unresolved model leaves the service unwired rather than failing boot.
    """
    from synthorg.engine.plan_review.reply import (  # noqa: PLC0415
        build_plan_item_reply_service,
    )
    from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415
    from synthorg.settings.state import config_resolver_of  # noqa: PLC0415

    if provider_registry is None:
        return
    resolver = config_resolver_of(app_state)
    service = build_plan_item_reply_service(
        reply_model=await resolver.get_str("coordination", "plan_review_reply_model"),
        temperature=await resolver.get_float(
            "coordination", "plan_review_reply_temperature"
        ),
        max_tokens=await resolver.get_int(
            "coordination", "plan_review_reply_max_tokens"
        ),
        timeout_seconds=_REPLY_TIMEOUT_SECONDS,
        provider_registry=provider_registry,
        cost_tracker=cost_tracker,
        config_resolver=resolver,
    )
    if service is not None:
        app_state.wire(EngineStateSlice, plan_item_reply_service=service)
        logger.info(API_APP_STARTUP, service="plan_item_reply_service", note="wired")
