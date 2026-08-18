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

import asyncio
import contextlib
import uuid
from collections.abc import Sequence
from datetime import datetime
from typing import Final
from uuid import UUID

from synthorg.api.channels import PlanNotifier
from synthorg.api.lifecycle_helpers.plan_questions import (
    PLAN_ID_METADATA_KEY,
    build_plan_questions,
    log_parked,
)
from synthorg.api.services.plan_service import PlanService
from synthorg.api.services.plan_service_factory import build_plan_service
from synthorg.api.state import AppState
from synthorg.api.subsystems.errors import SubsystemDeclinedError
from synthorg.approval.enums import ApprovalRiskLevel, ApprovalSource, ApprovalStatus
from synthorg.approval.plan_review import PLAN_APPROVAL_ACTION_TYPE
from synthorg.approval.protocol import ApprovalStoreProtocol
from synthorg.approval.state import approval_store_of
from synthorg.budget.session_budget import (
    SessionCeilings,
    resolve_session_token_ceiling,
)
from synthorg.budget.tracker_protocol import CostTrackerProtocol
from synthorg.core.approval import ApprovalItem
from synthorg.core.clock import Clock
from synthorg.core.concurrency import CASRetryHandler
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.display_name import UNNAMED_PROJECT
from synthorg.core.domain_errors import (
    PlanParentTaskMissingError,
    ResourceNotFoundError,
)
from synthorg.core.plan import Plan
from synthorg.core.plan_enums import PlanStatus
from synthorg.core.plan_review import PlanReviewOutcome
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
    PIPELINE_PLAN_APPROVAL_RETIRE_FAILED,
    PIPELINE_PLAN_FAIL_SHELL_MISSING,
    PIPELINE_PLAN_FAIL_WRITE_FAILED,
    PIPELINE_PLAN_MARKED_FAILED,
    PIPELINE_PLAN_PARENT_MISSING,
    PIPELINE_PLAN_SHELL_OPENED,
    PIPELINE_PROJECT_NOT_FOUND,
)
from synthorg.persistence.project_protocol import ProjectRepository
from synthorg.persistence.task_protocol import TaskRepository
from synthorg.providers.registry import ProviderRegistry

logger = get_logger(__name__)

_PLAN_ACTION_TYPE = PLAN_APPROVAL_ACTION_TYPE

#: The gate itself, as an actor: on the compensating FAILED write, and as the
#: requester of every approval it parks.
#:
#: On the FAILED write, no human asked for it: the pipeline is cleaning up
#: after a failure it already surfaced, and the ledger row should say so rather
#: than leave "who" blank, which the ledger reserves for a reconciler moving
#: something on its own schedule.
#:
#: On the approvals, this is the gate answering ``ApprovalItem.requested_by``'s
#: actual question, "agent or system that requested approval". The work item's
#: requester answers a different one ("agent name or user id that requested the
#: WORK"), and on the charter path it holds a user id, which the review surface
#: then renders as the asker's display name: the org signed its own questions
#: with the operator's primary key. Who filed the work stays where it belongs,
#: on ``Task.requested_by_user_id``.
_GATE_ACTOR: Final[str] = "plan_review_gate"

#: Stamped on the PLANNING shell, which is opened before anything has been
#: decomposed and so before there is anything to review. Stated rather than
#: left blank: an empty review section reads the same whether a plan passed
#: scrutiny or was never looked at, which is the ambiguity the outcome type
#: exists to remove. Replaced wholesale when the filled plan is parked.
_SHELL_NOT_YET_REVIEWED: Final[PlanReviewOutcome] = PlanReviewOutcome(
    absent_reason=NotBlankStr("the plan has not been decomposed yet"),
)

# Bounded compare-and-swap retries when the plan is reworked concurrently with
# its FAILED-compensation write, so a losing CAS re-reads and reapplies rather
# than aborting the one write meant to make a failure visible.
_MAX_FAIL_ATTEMPTS: Final[int] = 3

#: ``ApprovalItem.metadata`` key carrying resume context. The plan itself is
#: durable (referenced by ``PLAN_ID_METADATA_KEY``, re-exported here because
#: the resume and retire paths have always imported it from this module); the
#: approval only points at it.
PROJECT_METADATA_KEY = "project"

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


def _log_detached_compensation(done: asyncio.Future[None], plan_id: UUID) -> None:
    """Report the outcome of a compensation nobody is awaiting.

    A second cancellation during shutdown lands on the awaiting frame, not on
    the shielded task, so the compensation runs to completion with no one
    holding its result. Without this its failure is invisible and the
    approvals it was retiring stay PENDING against a plan reported failed.
    """
    if done.cancelled():
        logger.warning(
            PIPELINE_PLAN_APPROVAL_PARK_FAILED,
            plan_id=str(plan_id),
            error_type="CancelledError",
            error="parked-approval compensation was cancelled",
        )
        return
    exc = done.exception()
    if exc is not None:
        logger.warning(
            PIPELINE_PLAN_APPROVAL_PARK_FAILED,
            plan_id=str(plan_id),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            note="parked-approval compensation failed while detached",
        )


class PlanReviewApprovalGate:
    """Parks a decomposed plan as an approval item before a team builds.

    Structurally satisfies the engine's ``PlanReviewGate`` port; wired onto
    the work pipeline by the startup hook so the engine never imports the
    approval store. The plan is persisted durably and the parked approval
    carries only its ``plan_id``; on approval the plan is reloaded and rebuilt
    into a dispatch tree, so an operator's edits are what actually build.
    """

    __slots__ = (
        "_approval_store",
        "_clock",
        "_notifier",
        "_plans",
        "_projects",
        "_tasks",
    )

    def __init__(
        self,
        *,
        approval_store: ApprovalStoreProtocol,
        plans: PlanService,
        tasks: TaskRepository,
        projects: ProjectRepository,
        clock: Clock,
        notifier: PlanNotifier | None = None,
    ) -> None:
        # A service, not the repository: every status this gate writes is a
        # transition the lifecycle ledger has to carry, and a gate holding the
        # repository writes them where nothing records them.
        self._approval_store = approval_store
        self._plans = plans
        self._tasks = tasks
        self._projects = projects
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

    async def _project_name(self, project_id: NotBlankStr) -> NotBlankStr:
        """Resolve the human name of *project_id* for denormalisation.

        Args:
            project_id: The project the work item names.

        Returns:
            The project's name, or a word saying there is none. Never the id:
            this value is printed under the heading "project", and an id there
            reads as a name nobody chose. The absence is logged instead, where
            it is a fact about a plan opened against a project that is gone.
        """
        project = await self._projects.get(project_id)
        if project is not None:
            return project.name
        logger.warning(
            PIPELINE_PROJECT_NOT_FOUND,
            project=project_id,
            note="plan carries no project name; the project row is missing",
        )
        return NotBlankStr(UNNAMED_PROJECT)

    async def _provenance(
        self,
        work_item: WorkItem,
        task: Task,
        now: datetime,
        *,
        status: PlanStatus,
        review: PlanReviewOutcome,
    ) -> PlanProvenance:
        """Build the plan provenance shared by the shell and the filled plan.

        Returns:
            A :class:`PlanProvenance` stamping the objective / project identity,
            timing, lifecycle status, and (denormalised) review context.
        """
        return PlanProvenance(
            project=work_item.project,
            project_name=await self._project_name(work_item.project),
            objective_id=work_item.correlation_id,
            objective_title=NotBlankStr(task.title),
            parent_task_id=NotBlankStr(str(task.id)),
            created_at=now,
            status=status,
            forecast_id=work_item.forecast_id,
            review=review.review,
            review_absent_reason=review.absent_reason,
            objective_criteria=tuple(
                NotBlankStr(c.description) for c in task.acceptance_criteria
            ),
        )

    async def _require_parent(self, task: Task, plan_id: UUID) -> None:
        """Refuse to park a plan whose objective task is gone.

        Decomposition runs for minutes, and a delete landing in that window is
        invisible to the run itself: it completes against a deleted row and
        asks an operator to approve items under a task that 404s. The foreign
        key refuses the delete, so this catches what the constraint cannot: a
        row that predates it, or one removed by a path that bypasses the API.
        Checked before the approval is parked, because an orphan in the review
        queue can be neither approved nor removed.

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
            await self._provenance(
                work_item,
                task,
                now,
                status=PlanStatus.PLANNING,
                review=_SHELL_NOT_YET_REVIEWED,
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
        review: PlanReviewOutcome,
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
            CancelledError: Propagated once the approvals written so far are
                retired and the plan is failed, so a shutdown mid-park leaves
                nothing an operator can still act on.
        """
        await self._require_parent(task, plan_id)
        approval_id = uuid.uuid4()
        detail = _plan_detail(plan)
        now = self._clock.now()
        shell = await self._plans.get(NotBlankStr(str(plan_id)))
        filled = plan_from_decomposition(
            plan,
            await self._provenance(
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
        # A lost shell (opened on a prior boot, then pruned) persists the
        # filled plan fresh so the approval still references a durable plan
        # rather than dangling; the service owns that fork.
        await self._plans.record_decomposed(durable_plan, shell=shell)
        approval = ApprovalItem(
            id=approval_id,
            action_type=NotBlankStr(_PLAN_ACTION_TYPE),
            title=NotBlankStr(f"Approve plan for: {task.title}"),
            description=NotBlankStr(detail),
            requested_by=NotBlankStr(_GATE_ACTOR),
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
        parked: list[ApprovalItem] = []
        try:
            await self._approval_store.add(approval)
            parked.append(approval)
            # Parked after the plan approval, and inside the same guard: a
            # question nobody can answer is the state this whole path exists
            # to close, so a failure here fails the plan rather than parking
            # an approval whose open questions reach nobody.
            questions = build_plan_questions(
                durable_plan,
                task_id=NotBlankStr(str(task.id)),
                requested_by=NotBlankStr(_GATE_ACTOR),
                now=now,
            )
            for question in questions:
                await self._approval_store.add(question)
                parked.append(question)
            log_parked(durable_plan, len(questions))
        except asyncio.CancelledError:
            # Cancellation reaches here as a BaseException, so the handler
            # below never sees it: a shutdown between two of these writes
            # would leave every approval written so far PENDING against a
            # plan stuck in PENDING_REVIEW. The compensation is shielded
            # because the cancellation that got us here would cancel it too.
            logger.warning(
                PIPELINE_PLAN_APPROVAL_PARK_FAILED,
                plan_id=str(durable_plan.id),
                error_type="CancelledError",
                error="approval parking was cancelled",
            )
            compensation = asyncio.ensure_future(
                self._abandon_parked(
                    parked,
                    plan_id=durable_plan.id,
                    reason="approval parking was cancelled",
                )
            )
            # A second cancellation during shutdown lands on the await, not on
            # the shielded task, so the compensation keeps running with nobody
            # holding it. Its outcome is reported from a done-callback rather
            # than only from here, otherwise those approvals would stay PENDING
            # against a plan reported failed and nothing would say so.
            compensation.add_done_callback(
                lambda done: _log_detached_compensation(done, durable_plan.id)
            )
            with contextlib.suppress(asyncio.CancelledError):
                await asyncio.shield(compensation)
            raise
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
            await self._abandon_parked(
                parked,
                plan_id=durable_plan.id,
                reason="approval-store write failed",
            )
            raise
        self._announce(durable_plan)
        return PlanReviewHandoff(
            approval_id=NotBlankStr(str(approval_id)),
            plan_id=NotBlankStr(str(durable_plan.id)),
            subtask_count=len(plan.plan.subtasks),
            detail=NotBlankStr(detail),
        )

    async def _abandon_parked(
        self,
        parked: Sequence[ApprovalItem],
        *,
        plan_id: UUID,
        reason: str,
    ) -> None:
        """Retire what parked, then fail the plan, in that order.

        Retiring first is the load-bearing half: an approval that outlives
        its plan is still actionable, and answering one writes back onto a
        plan the operator is being told failed.

        Args:
            parked: The approvals written before the park failed.
            plan_id: The durable plan the failed park belongs to.
            reason: What the operator is told failed the plan.
        """
        await self._retire_parked(parked)
        await self.fail_plan(plan_id=plan_id, reason=reason)

    async def _retire_parked(self, parked: Sequence[ApprovalItem]) -> None:
        """Remove approvals written before a later park failed.

        Parking is several writes and the store has no batch, so a failure
        partway leaves PENDING approvals for a plan that is about to be
        FAILED. Those are not inert: the plan approval still offers approve
        and reject, and answering a question writes back onto the plan
        through :func:`apply_plan_question_answer`. Removing them is what
        makes the failure the whole outcome rather than half of one.

        Best-effort per row, like ``fail_plan``: the caller is already
        re-raising the write failure that brought it here, and a compensation
        that raises would replace that diagnosis with its own.

        Args:
            parked: The approvals this call had already written, in order.
        """
        for item in parked:
            try:
                await self._approval_store.delete(NotBlankStr(str(item.id)))
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                # lint-allow: swallow-ok -- compensation for a failure already
                # being raised; replacing that diagnosis would hide the cause.
                reraise_critical(exc)
                logger.warning(
                    PIPELINE_PLAN_APPROVAL_RETIRE_FAILED,
                    approval_id=str(item.id),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
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
            # Through the service, not the repository: this is the write that
            # ends a plan, and it is the one an operator asks about months
            # later. Its sibling on the resume path already routes here, so a
            # raw write made the ledger look complete while the compensating
            # failure was the row missing from it.
            failed = await self._plans.sync_status(
                plan,
                PlanStatus.FAILED,
                requested_by=_GATE_ACTOR,
                reason=str(marked_reason),
                failure_reason=marked_reason,
            )
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

    Raises:
        SubsystemDeclinedError: The gate is not required, or a collaborator
            it parks approvals through is absent.
    """
    from synthorg.approval.state import ApprovalStateSlice  # noqa: PLC0415
    from synthorg.engine.state import (  # noqa: PLC0415
        EngineStateSlice,
        work_pipeline_of,
    )
    from synthorg.persistence.state import PersistenceStateSlice  # noqa: PLC0415
    from synthorg.settings.state import config_resolver_of  # noqa: PLC0415

    if app_state.slice(EngineStateSlice).work_pipeline is None:
        msg = "no work pipeline; the gate attaches to it"
        raise SubsystemDeclinedError(msg)
    required = await config_resolver_of(app_state).get_bool(
        "coordination", "plan_approval_required"
    )
    if not required:
        msg = "coordination.plan_approval_required is off"
        raise SubsystemDeclinedError(msg)
    # The approval store + persistence backend are normally wired by the time
    # this hook runs, but an early boot (before persistence connects) can reach
    # here without them. Declining beats letting an accessor raise a 503 out of
    # a wiring hook. The operator explicitly opted into a mandatory gate, so
    # the reason is warn-worthy: without the gate every splittable plan builds
    # ungated, and a silent skip would hide that regression.
    if app_state.slice(ApprovalStateSlice).store is None:
        msg = "plan_approval_required is on but no approval store is wired"
        logger.warning(API_APP_STARTUP, service="plan_review_gate", note=msg)
        raise SubsystemDeclinedError(msg)
    backend = app_state.slice(PersistenceStateSlice).backend
    if backend is None:
        msg = "plan_approval_required is on but no persistence backend is wired"
        logger.warning(API_APP_STARTUP, service="plan_review_gate", note=msg)
        raise SubsystemDeclinedError(msg)
    from synthorg.api.api_core_state import ApiCoreStateSlice  # noqa: PLC0415

    gate = PlanReviewApprovalGate(
        approval_store=approval_store_of(app_state),
        plans=build_plan_service(backend, clock=app_state.clock),
        tasks=backend.tasks,
        projects=backend.projects,
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

    Raises:
        SubsystemDeclinedError: The panel is switched off, or a collaborator
            it reviews through is absent.
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
        msg = "no work pipeline; the panel runs inside its gated-plan flow"
        raise SubsystemDeclinedError(msg)
    resolver = config_resolver_of(app_state)
    if not await resolver.get_bool("coordination", "plan_review_panel_enabled"):
        msg = "coordination.plan_review_panel_enabled is off"
        raise SubsystemDeclinedError(msg)
    if provider_registry is None:
        msg = "no provider registry; every panellist verdict is an LLM call"
        raise SubsystemDeclinedError(msg)
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
        max_revision_rounds=await resolver.get_int(
            "coordination", "plan_review_max_revision_rounds"
        ),
        ceilings=SessionCeilings.of(
            cost_ceiling=await resolver.get_float(
                "coordination", "plan_review_panel_cost_ceiling"
            ),
            token_ceiling=await resolve_session_token_ceiling(resolver),
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


async def unwire_plan_review_panel(app_state: AppState) -> None:
    """Detach the plan-review panel from the work pipeline.

    The teardown half of the panel's rebuild: its bounds, panel size and
    turn cap are baked in at construction, so an operator's write only
    reaches a running system if the built instance is replaced.
    """
    from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415

    pipeline = app_state.slice(EngineStateSlice).work_pipeline
    if pipeline is None:
        return
    pipeline.attach_plan_review_panel(None)
    logger.info(API_APP_STARTUP, service="plan_review_panel", note="unwired")
