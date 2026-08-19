# module-kind: complex_service
"""Default work pipeline implementation.

Composes the already-wired runtime services into the single spine:
intake -> projects -> decompose (solo-vs-team verdict) -> solo OR
team execution -> coordination metrics. The spine owns no
user-facing choice; the verdict is produced by the injected
:class:`WorkRoutingPolicy`. Coordination metrics are emitted by the
shared collector already threaded into the boot ``AgentEngine`` and
the coordinator; the spine records the stage but never re-collects.
"""

from collections.abc import Awaitable
from typing import TYPE_CHECKING, Final, NamedTuple, TypeVar
from uuid import UUID

from synthorg.client.models import ClientRequest, TaskRequirement
from synthorg.core.agent import AgentIdentity
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.concurrency.refcounted_lock_map import RefcountedLockMap
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.persistence_errors import PersistenceVersionConflictError
from synthorg.core.plan_review import PlanReviewOutcome
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.assignment.service import TaskAssignmentService
from synthorg.engine.coordination.models import CoordinationContext
from synthorg.engine.coordination.service import MultiAgentCoordinator
from synthorg.engine.decomposition.models import (
    DecompositionContext,
    DecompositionResult,
    roster_from_agents,
)
from synthorg.engine.errors import ProjectNotFoundError
from synthorg.engine.intake.engine import IntakeEngine
from synthorg.engine.pipeline._initiative_authorisation import (
    require_authorised_initiative,
)
from synthorg.engine.pipeline._owner_selection import select_project_owner
from synthorg.engine.pipeline._solo_selection import select_solo_agent
from synthorg.engine.pipeline.charter_authority_port import CharterAuthority
from synthorg.engine.pipeline.errors import (
    WorkIntakeRejectedError,
    WorkPipelineError,
    WorkPipelineTeamPathUnavailableError,
    WorkRoutingUndecidableError,
)
from synthorg.engine.pipeline.models import (
    ExecutionPath,
    PipelineAttachments,
    PlanReviewHandoff,
    RefinementHandoff,
    RoutingVerdict,
    WorkItem,
    WorkPhaseResult,
    WorkPipelineResult,
    WorkSource,
)
from synthorg.engine.pipeline.narrator_port import RunNarrator
from synthorg.engine.pipeline.plan_review_panel_port import PlanReviewPanel
from synthorg.engine.pipeline.plan_review_port import PlanReviewGate
from synthorg.engine.pipeline.plan_revision import (
    build_reviewed_plan,
    review_phase_name,
)
from synthorg.engine.pipeline.policy.protocol import WorkRoutingPolicy
from synthorg.engine.pipeline.refinement_port import WorkRefinementRouter
from synthorg.engine.roster import AvailableRoster
from synthorg.engine.routing.scorer import AgentTaskScorer
from synthorg.engine.stakes import build_stakes_assessor
from synthorg.engine.stakes.protocol import StakesAssessor
from synthorg.engine.task_engine import TaskEngine
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.chief_of_staff import (
    COS_NARRATIVE_GENERATION_FAILED,
    COS_NARRATIVE_SKIPPED,
)
from synthorg.observability.events.pipeline import (
    PIPELINE_PHASE_COMPLETED,
    PIPELINE_PHASE_FAILED,
    PIPELINE_PLAN_DECOMPOSITION_FAILED,
    PIPELINE_PLAN_FAIL_TRANSITION_FAILED,
    PIPELINE_PLAN_REVIEW_PANEL_ATTACHED,
    PIPELINE_PLAN_REVIEW_PANEL_FAILED,
    PIPELINE_PLAN_REVIEW_REQUESTED,
    PIPELINE_PROJECT_LEAD_CONTENDED,
    PIPELINE_PROJECT_LEAD_ORPHANED,
    PIPELINE_PROJECT_LEAD_STAMPED,
    PIPELINE_PROJECT_LEAD_UNAVAILABLE,
    PIPELINE_PROJECT_NOT_FOUND,
    PIPELINE_PROJECT_ROSTER_EMPTY,
    PIPELINE_REFINEMENT_REQUESTED,
    PIPELINE_ROUTING_UNDECIDABLE,
    PIPELINE_RUN_COMPLETED,
    PIPELINE_RUN_FAILED,
    PIPELINE_RUN_STARTED,
    PIPELINE_TASK_MISSING,
    PIPELINE_TEAM_PATH_UNAVAILABLE,
    PIPELINE_WORK_INTAKE_REJECTED,
)
from synthorg.observability.events.stakes_routing import STAKES_ASSESSED
from synthorg.persistence.project_protocol import ProjectRepository

if TYPE_CHECKING:
    from synthorg.workers.execution_service import WorkerExecutionService

logger = get_logger(__name__)

_T = TypeVar("_T")

_PHASE_INTAKE = "intake"
_PHASE_PROJECTS = "projects"
_PHASE_DECOMPOSE = "decompose"
_PHASE_SOLO = "solo_execution"
_PHASE_TEAM = "team_execution"
_PHASE_REFINE = "refinement_handoff"
_PHASE_PLAN_REVIEW = "plan_review_handoff"
_PHASE_METRICS = "coordination_metrics"

#: Recorded on the durable plan when no panel is wired at all, so the
#: operator approving it can tell an unreviewed plan from an unobjectionable
#: one; an empty review section alone cannot separate them.
_NO_PANEL_ATTACHED = (
    "no stakeholder review panel is configured, so this plan carries no review"
)

#: Bounded compare-and-swap retries when a concurrent process stamps a
#: project's lead between our read and our version-guarded write; the re-read
#: then observes the winning lead, so a couple of attempts always resolves.
_MAX_STAFF_ATTEMPTS: Final[int] = 3

#: Work sources whose ``requested_by`` is a human user id (not an agent or
#: system identity). Only these stamp ``Task.requested_by_user_id`` so the SSE
#: event-stream endpoint can resolve session ownership; agent/system sources
#: leave it ``None`` (no human owner, so only a CEO may stream them).
_HUMAN_ORIGINATED_SOURCES: frozenset[WorkSource] = frozenset(
    {WorkSource.TASK_BOARD, WorkSource.CONVERSATIONAL}
)


class _ExecutionOutcome(NamedTuple):
    """Result of the routed execution path within a single pipeline run."""

    path: ExecutionPath
    final_status: TaskStatus
    refinement_handoff: RefinementHandoff | None
    plan_review_handoff: PlanReviewHandoff | None


class DefaultWorkPipeline:
    """The shipped :class:`WorkPipeline` implementation.

    Args:
        intake_engine: Walks a request through intake.
        task_engine: Single-writer task state owner.
        project_repository: Read seam for project-context binding.
        routing_policy: Owns the solo-vs-team verdict.
        scorer: Shared agent-task scorer (also used by the
            coordinator) for the solo-path single-agent pick.
        worker_execution_service: Solo-path executor.
        coordinator: Team-path coordinator; ``None`` in an
            empty-company boot.
        roster: Staffable-agent pool source: the active agents whose bound
            model can currently serve work.
        clock: Injectable time source (defaults to the system clock).
    """

    __slots__ = (
        "_assignment_service",
        "_charter_authority",
        "_clock",
        "_coordinator",
        "_intake_engine",
        "_narrator",
        "_plan_review_gate",
        "_plan_review_panel",
        "_project_locks",
        "_project_repository",
        "_refinement_router",
        "_roster",
        "_routing_policy",
        "_scorer",
        "_stakes_assessor",
        "_task_engine",
        "_worker_execution_service",
    )

    def __init__(  # noqa: PLR0913 -- keyword-only dependency injection
        self,
        *,
        intake_engine: IntakeEngine,
        task_engine: TaskEngine,
        project_repository: ProjectRepository,
        routing_policy: WorkRoutingPolicy,
        scorer: AgentTaskScorer,
        worker_execution_service: WorkerExecutionService,
        coordinator: MultiAgentCoordinator | None,
        roster: AvailableRoster,
        clock: Clock | None = None,
        stakes_assessor: StakesAssessor | None = None,
        assignment_service: TaskAssignmentService | None = None,
    ) -> None:
        self._intake_engine = intake_engine
        self._task_engine = task_engine
        self._project_repository = project_repository
        self._routing_policy = routing_policy
        self._scorer = scorer
        self._worker_execution_service = worker_execution_service
        self._coordinator = coordinator
        self._roster = roster
        self._clock = clock if clock is not None else SystemClock()
        self._stakes_assessor = stakes_assessor or build_stakes_assessor()
        # Solo-path assignment service: when wired, the single-agent
        # pick routes through ``TaskAssignmentService`` so its status
        # validation and the capability ladder run. Absent, the pick falls
        # through to the shared scorer with neither.
        self._assignment_service = assignment_service
        self._narrator: RunNarrator | None = None
        self._refinement_router: WorkRefinementRouter | None = None
        self._plan_review_gate: PlanReviewGate | None = None
        self._plan_review_panel: PlanReviewPanel | None = None
        self._charter_authority: CharterAuthority | None = None
        # Serialises the owner read-modify-write per project so two concurrent
        # work items for the same project cannot both observe an unled project
        # and race to stamp a different lead (lost update on ``Project.lead``).
        self._project_locks: RefcountedLockMap[str] = RefcountedLockMap()

    def attach_charter_authority(self, authority: CharterAuthority | None) -> None:
        """Attach (or clear) the store that says whether a charter was approved.

        Late-bind seam: charters live above the spine and their store wires
        only after persistence connects. Absent, a brief that forces a plan
        is REFUSED rather than run, because the alternative is standing up an
        initiative on an authorisation nothing checked. Nothing legitimate is
        lost by that: the charter dispatcher is the only producer of such a
        brief and it needs the same store to exist at all.
        """
        self._charter_authority = authority

    def attach_narrator(self, narrator: RunNarrator | None) -> None:
        """Attach (or clear) the post-run narrator (documentary mode).

        Late-bind seam: the narrator depends on services that wire only
        after persistence connects, so it is attached to the already-built
        pipeline by the startup hook rather than passed at construction.
        Passing ``None`` detaches it, which is how the reconciler takes the
        narrator down when the docs engine or project brain it captured is
        replaced.
        """
        self._narrator = narrator

    def attach_refinement_router(self, router: WorkRefinementRouter | None) -> None:
        """Attach (or clear) the under-specified-team-work refinement router.

        Late-bind seam: the router wraps the Chief-of-Staff proposer,
        which wires only after persistence and a provider are available,
        so it is attached to the already-built pipeline by the startup
        hook. Absent, team-bound work with no definition of done falls
        through to the coordinator, where the clarification gate blocks it.
        Passing ``None`` detaches it, which is how the reconciler takes the
        router down when the proposer it wraps is replaced: the router binds
        that proposer at construction, so a stale one would keep refining
        through the instance the operator's model change replaced.
        """
        self._refinement_router = router

    def attach_plan_review_gate(self, gate: PlanReviewGate) -> None:
        """Attach the human plan-approval gate for splittable team work.

        Late-bind seam: the gate wraps the approval/conversational surface,
        which wires only after persistence is available, so it is attached to
        the already-built pipeline by the startup hook. Absent, splittable
        team work dispatches straight to the coordinator (no human plan gate);
        present, the decomposed plan is parked for approval before any team
        builds, and the approved plan is dispatched verbatim on approval.
        """
        self._plan_review_gate = gate

    def attach_plan_review_panel(self, panel: PlanReviewPanel | None) -> None:
        """Attach (or clear) the stakeholder plan-review panel for gated plans.

        Late-bind seam: the panel wraps a completion provider, which wires only
        after persistence and a provider are available, so it is attached to
        the already-built pipeline by the startup hook. Absent, a gated plan is
        parked for human approval with no panel review; present, a bounded
        panel of leads reviews the plan and their consolidated verdict is
        attached to the durable plan before the human sees it.
        Passing ``None`` detaches it, which is how the reconciler takes the
        panel down before rebuilding it: the panel bakes its bounds and its
        provider selector in at construction, so a stale one would keep
        reviewing under the ceilings the operator's write replaced.
        """
        self._plan_review_panel = panel
        if panel is not None:
            logger.info(PIPELINE_PLAN_REVIEW_PANEL_ATTACHED)

    @property
    def attachments(self) -> PipelineAttachments:
        """Report which late-bound collaborators are currently attached.

        Returns:
            A :class:`PipelineAttachments` read straight off the fields the
            ``attach_*`` seams write, so it cannot claim an attachment that
            is not there.
        """
        return PipelineAttachments(
            narrator=self._narrator is not None,
            refinement_router=self._refinement_router is not None,
            plan_review_gate=self._plan_review_gate is not None,
            plan_review_panel=self._plan_review_panel is not None,
            charter_authority=self._charter_authority is not None,
        )

    async def run(self, work_item: WorkItem) -> WorkPipelineResult:
        """Drive *work_item* through the full spine (see module docstring).

        Returns:
            A :class:`WorkPipelineResult` carrying the verdict,
            execution path, final task status, per-phase timings, and
            total wall-clock duration.

        Raises:
            WorkIntakeRejectedError: If intake rejects the request or
                does not persist a task.
            ProjectNotFoundError: If ``work_item.project`` does not
                resolve.
            WorkRoutingUndecidableError: If no viable execution path or
                solo agent can be selected.
            WorkPipelineTeamPathUnavailableError: If team execution is
                required but no coordinator is wired.
            WorkPipelineError: If execution completes without a readable
                terminal task state.
        """
        started = self._clock.monotonic()
        phases: list[WorkPhaseResult] = []
        await require_authorised_initiative(work_item, self._charter_authority)
        logger.info(
            PIPELINE_RUN_STARTED,
            correlation_id=work_item.correlation_id,
            source=work_item.source.value,
            project=work_item.project,
        )
        task = await self._phase(phases, _PHASE_INTAKE, self._intake(work_item))
        return await self._continue_from_task(work_item, task, phases, started)

    async def intake_only(self, work_item: WorkItem) -> Task:
        """Run only the intake phase and return the created task.

        Persists the task (stamping the human owner so the AG-UI event
        stream can resolve session ownership) and returns it without running
        decomposition or execution. Lets a caller surface the task id and
        subscribe to its progress stream, then background the remaining spine
        via :meth:`continue_from_intake`.

        Returns:
            The task created by intake.

        Raises:
            WorkIntakeRejectedError: If intake rejects the request or does
                not persist a task.
            WorkInitiativeUnauthorisedError: If the brief stands up an
                initiative no operator approved.
        """
        await require_authorised_initiative(work_item, self._charter_authority)
        logger.info(
            PIPELINE_RUN_STARTED,
            correlation_id=work_item.correlation_id,
            source=work_item.source.value,
            project=work_item.project,
        )
        return await self._intake(work_item)

    async def continue_from_intake(
        self, work_item: WorkItem, task: Task
    ) -> WorkPipelineResult:
        """Run the pipeline from an already-created task (intake complete).

        The counterpart to :meth:`intake_only`: resolves the project,
        decomposes, and executes the solo/team/refine/plan-review path.
        Used by the conversational-intake path, which runs intake
        synchronously (to surface the task id) then backgrounds this.

        Returns:
            The :class:`WorkPipelineResult` for the run.
        """
        return await self._continue_from_task(
            work_item, task, [], self._clock.monotonic()
        )

    async def _continue_from_task(
        self,
        work_item: WorkItem,
        task: Task,
        phases: list[WorkPhaseResult],
        started: float,
    ) -> WorkPipelineResult:
        """Run the post-intake spine (project -> decompose -> execute).

        Returns:
            The :class:`WorkPipelineResult` for the run.

        Raises:
            ProjectNotFoundError: If ``work_item.project`` does not resolve.
            WorkRoutingUndecidableError: If no viable path/agent is selected.
            WorkPipelineTeamPathUnavailableError: If team execution is
                required but no coordinator is wired.
            WorkPipelineError: If execution completes without a readable
                terminal task state.
        """
        try:
            owner, active = await self._phase(
                phases, _PHASE_PROJECTS, self._resolve_project(work_item, task)
            )
            verdict, agents = await self._phase(
                phases, _PHASE_DECOMPOSE, self._decompose(task, active)
            )
            # A charter/objective is a brief to be planned, so it must never
            # collapse to a single solo agent: force the splittable path and
            # let the solo-vs-team router decide only how each child task in
            # the resulting plan runs.
            if work_item.plan_required:
                verdict = RoutingVerdict.SPLITTABLE
            # The mirror: an integration brief is one accountable assembly job.
            # Splitting it would hand the pieces back to separate agents, which
            # is exactly the state the stage exists to end.
            elif work_item.leaf_required:
                verdict = RoutingVerdict.LEAF
            outcome = await self._execute_selected_path(
                work_item,
                task,
                agents=agents,
                verdict=verdict,
                phases=phases,
                owner=owner,
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                PIPELINE_RUN_FAILED,
                correlation_id=work_item.correlation_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        total = max(0.0, self._clock.monotonic() - started)
        result = WorkPipelineResult(
            work_item=work_item,
            verdict=verdict,
            execution_path=outcome.path,
            task_id=str(task.id),
            final_task_status=outcome.final_status,
            phases=tuple(phases),
            refinement_handoff=outcome.refinement_handoff,
            plan_review_handoff=outcome.plan_review_handoff,
            total_duration_seconds=total,
        )
        logger.info(
            PIPELINE_RUN_COMPLETED,
            correlation_id=work_item.correlation_id,
            task_id=str(task.id),
            verdict=verdict.value,
            execution_path=outcome.path.value,
            final_task_status=outcome.final_status.value,
            total_duration_seconds=total,
        )
        await self._try_generate_narrative(work_item, task)
        return result

    async def _execute_selected_path(
        self,
        work_item: WorkItem,
        task: Task,
        *,
        agents: tuple[AgentIdentity, ...],
        verdict: RoutingVerdict,
        phases: list[WorkPhaseResult],
        owner: AgentIdentity | None,
    ) -> _ExecutionOutcome:
        """Run the routed execution path (solo / refine / plan-review / team).

        Returns:
            The :class:`_ExecutionOutcome` naming the path taken, the final
            task status, and any refinement / plan-review handoff produced.
        """
        if verdict is RoutingVerdict.LEAF:
            final_status = await self._phase(
                phases, _PHASE_SOLO, self._run_solo(work_item, task, agents)
            )
            await self._phase(phases, _PHASE_METRICS, self._metrics_stage())
            return _ExecutionOutcome(ExecutionPath.SOLO, final_status, None, None)
        if self._should_refine(task):
            # Team-bound work with no definition of done: refine with a human
            # before mobilising a team (the clarification gate would otherwise
            # block decomposition). No coordination runs, so the
            # coordination-metrics stage is skipped.
            refine_handoff = await self._phase(
                phases, _PHASE_REFINE, self._refine(work_item, task)
            )
            return _ExecutionOutcome(
                ExecutionPath.REFINEMENT, task.status, refine_handoff, None
            )
        if self._should_gate_plan():
            # Splittable team work under a human plan-approval gate: persist a
            # plan shell, decompose to fill it, park it for approval, and stop.
            # Nothing builds until the plan is approved, at which point the
            # durable plan (with any operator edits) is rebuilt and dispatched.
            # A failed decomposition marks the shell FAILED (approval_id=None)
            # and the run is reported as unsuccessful (final status FAILED)
            # rather than raising a 500 -- the failure surfaces as a visible
            # FAILED plan, not a silent orphan.
            plan_handoff = await self._phase(
                phases,
                _PHASE_PLAN_REVIEW,
                self._plan_review(work_item, task, agents, phases, owner),
            )
            final_status = (
                task.status
                if plan_handoff.approval_id is not None
                else TaskStatus.FAILED
            )
            return _ExecutionOutcome(
                ExecutionPath.PLAN_REVIEW, final_status, None, plan_handoff
            )
        final_status = await self._phase(
            phases, _PHASE_TEAM, self._run_team(work_item, task, agents, owner)
        )
        await self._phase(phases, _PHASE_METRICS, self._metrics_stage())
        return _ExecutionOutcome(ExecutionPath.TEAM, final_status, None, None)

    def _coordination_context(
        self,
        task: Task,
        agents: tuple[AgentIdentity, ...],
        owner: AgentIdentity | None,
    ) -> CoordinationContext:
        """Build the coordination context, threading the staffed owner.

        The owner rides on the ``DecompositionContext`` so an agent-session
        decomposition strategy plans AS the owner; a single-shot strategy
        simply ignores it. The roster rides alongside so the planner selects
        an owning role per item rather than inventing one nothing can be
        dispatched to.

        Returns:
            A :class:`CoordinationContext` carrying the owner and the roster
            on its decomposition context.
        """
        return CoordinationContext(
            task=task,
            available_agents=agents,
            decomposition_context=DecompositionContext(
                owner_identity=owner,
                available_roles=roster_from_agents(agents),
            ),
        )

    async def _try_generate_narrative(self, work_item: WorkItem, task: Task) -> None:
        """Generate the run narrative, best-effort.

        Documentary mode is opt-in and never blocks or fails the run: a
        missing narrator is a no-op, and any error degrades to a logged
        warning. Critical interpreter errors still propagate.
        """
        # Snapshot once: a reconciler rebuild can detach the narrator between
        # the null-check and the call, so a single load keeps both on the same
        # value rather than calling one that has since become None.
        narrator = self._narrator
        if narrator is None:
            logger.debug(
                COS_NARRATIVE_SKIPPED,
                correlation_id=work_item.correlation_id,
                task_id=str(task.id),
                reason="no_narrator_attached",
            )
            return
        try:
            await narrator.generate(task_id=str(task.id), project_id=work_item.project)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- best-effort documentary narrative
            reraise_critical(exc)
            logger.warning(
                COS_NARRATIVE_GENERATION_FAILED,
                correlation_id=work_item.correlation_id,
                task_id=str(task.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def _phase(
        self,
        phases: list[WorkPhaseResult],
        name: str,
        awaitable: Awaitable[_T],
    ) -> _T:
        """Await *awaitable*, recording a timed :class:`WorkPhaseResult`.

        Returns:
            The value produced by ``awaitable`` on success; on failure
            a :class:`WorkPhaseResult` is appended to ``phases`` and
            the exception propagates.
        """
        start = self._clock.monotonic()
        try:
            value = await awaitable
        except Exception as exc:
            reraise_critical(exc)
            elapsed = max(0.0, self._clock.monotonic() - start)
            phases.append(
                WorkPhaseResult(
                    phase=name,
                    success=False,
                    duration_seconds=elapsed,
                    error=safe_error_description(exc),
                )
            )
            logger.warning(
                PIPELINE_PHASE_FAILED,
                phase=name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise
        elapsed = max(0.0, self._clock.monotonic() - start)
        phases.append(
            WorkPhaseResult(phase=name, success=True, duration_seconds=elapsed)
        )
        logger.info(PIPELINE_PHASE_COMPLETED, phase=name, duration_seconds=elapsed)
        return value

    async def _intake(self, work_item: WorkItem) -> Task:
        """Map the work item through intake and return the created task.

        Returns:
            The persisted :class:`Task`, with forecast linkage and
            parent-task stakes already stamped.

        Raises:
            WorkIntakeRejectedError: If intake rejects the request,
                produces no ``task_id``, or the task is not persisted.
        """
        request = ClientRequest(
            request_id=work_item.correlation_id,
            client_id=work_item.origin_adapter_id,
            requirement=TaskRequirement(
                title=work_item.title,
                description=work_item.raw_intent,
                task_type=work_item.task_type,
                priority=work_item.priority,
                estimated_complexity=work_item.estimated_complexity,
                acceptance_criteria=work_item.acceptance_criteria,
            ),
            metadata={
                "source": work_item.source.value,
                "project": work_item.project,
                "requested_by": work_item.requested_by,
            },
        )
        _, result = await self._intake_engine.process(request)
        if not result.accepted:
            reason = result.rejection_reason or "intake rejected the request"
            logger.warning(
                PIPELINE_WORK_INTAKE_REJECTED,
                project=work_item.project,
                reason=reason,
                error_type=WorkIntakeRejectedError.__name__,
            )
            raise WorkIntakeRejectedError(reason)
        if result.task_id is None:
            msg = "intake accepted the request but produced no task id"
            logger.warning(
                PIPELINE_WORK_INTAKE_REJECTED,
                project=work_item.project,
                reason="no_task_id",
                error_type=WorkIntakeRejectedError.__name__,
            )
            raise WorkIntakeRejectedError(msg)
        task = await self._task_engine.get_task(result.task_id)
        if task is None:
            msg = f"intake reported task {result.task_id!r} but it is not persisted"
            logger.warning(
                PIPELINE_WORK_INTAKE_REJECTED,
                project=work_item.project,
                task_id=str(result.task_id),
                reason="task_not_persisted",
                error_type=WorkIntakeRejectedError.__name__,
            )
            raise WorkIntakeRejectedError(msg)
        task = await self._stamp_requester(task, work_item)
        task = await self._link_forecast(task, work_item)
        return await self._assess_stakes(task, work_item)

    async def _stamp_requester(self, task: Task, work_item: WorkItem) -> Task:
        """Stamp the human requester's user id for SSE session ownership.

        Only human-originated work (task board, conversational) carries a
        real user id in ``requested_by``; agent and system sources carry an
        agent/system identity instead, so their tasks keep
        ``requested_by_user_id=None`` and only a CEO may subscribe to their
        event stream.

        Returns:
            The task with ``requested_by_user_id`` set for human-originated
            work, otherwise the task unchanged.
        """
        if work_item.source not in _HUMAN_ORIGINATED_SOURCES:
            return task
        if task.requested_by_user_id == work_item.requested_by:
            return task
        return await self._task_engine.update_task(
            str(task.id),
            {"requested_by_user_id": work_item.requested_by},
            requested_by=work_item.requested_by,
        )

    async def _assess_stakes(self, task: Task, work_item: WorkItem) -> Task:
        """Assess and stamp parent-task stakes for the LEAF (solo) path.

        The decomposition service assesses each subtask on the team path,
        but a LEAF task is executed directly without decomposition, so the
        parent task itself must carry its stakes for the routing layer.
        Stamped here, at the single intake funnel, so both paths converge.

        Returns:
            The task with its ``stakes`` field updated; the original
            ``task`` is returned unchanged when the assessor produces
            the same stakes already on the task.
        """
        stakes = self._stakes_assessor.assess_task(task)
        if stakes is task.stakes:
            return task
        updated = await self._task_engine.update_task(
            str(task.id),
            {"stakes": stakes},
            requested_by=work_item.requested_by,
        )
        logger.info(
            STAKES_ASSESSED,
            task_id=str(task.id),
            from_stakes=task.stakes.value,
            to_stakes=stakes.value,
            path="leaf",
        )
        return updated

    async def _link_forecast(self, task: Task, work_item: WorkItem) -> Task:
        """Stamp the approved forecast id + ceiling onto the task.

        The forecast gate releases an approved work item carrying its
        ``forecast_id`` and the operator-approved ``hard_ceiling``. The
        intake engine creates the task without those, so persist them
        here: the in-loop ``BudgetChecker`` reads ``Task.hard_ceiling``
        to enforce the per-brief ceiling, and the engine reads
        ``Task.forecast_id`` to stamp halt context for the resume banner.

        Returns:
            The task with the forecast linkage applied; the original
            ``task`` is returned unchanged when neither field is set
            on ``work_item``.
        """
        updates: dict[str, object] = {}
        if work_item.forecast_id is not None:
            updates["forecast_id"] = work_item.forecast_id
        if work_item.hard_ceiling is not None:
            updates["hard_ceiling"] = work_item.hard_ceiling
        if not updates:
            return task
        return await self._task_engine.update_task(
            str(task.id),
            updates,
            requested_by=work_item.requested_by,
        )

    async def _resolve_project(
        self, work_item: WorkItem, task: Task
    ) -> tuple[AgentIdentity | None, tuple[AgentIdentity, ...]]:
        """Bind the work to its project and staff an accountable owner.

        Beyond the existence check, this staffs a single owner for a planned
        initiative from the standing roster: a greenlit objective is owned,
        never run anonymously. The owner is stamped as the project's durable
        ``lead`` (idempotently: an already-led project keeps its lead) and
        returned so the planning stage can run AS the owner. The active roster
        is read once here and threaded out so the decompose phase reuses it
        rather than issuing a second registry read per run.

        Returns:
            A ``(owner, active_agents)`` pair. ``owner`` is ``None`` when the
            work is a one-off leaf task (not a planned initiative), when the
            roster is empty, or when an already-led project's lead no longer
            resolves to a known agent (an orphaned lead). ``active_agents`` is
            the active roster snapshot for the run.

        Raises:
            ProjectNotFoundError: If the project referenced by
                ``work_item.project`` is not in the project repository.
        """
        project = await self._project_repository.get(work_item.project)
        if project is None:
            logger.warning(
                PIPELINE_PROJECT_NOT_FOUND,
                project=work_item.project,
                error_type=ProjectNotFoundError.__name__,
            )
            raise ProjectNotFoundError(project_id=work_item.project)
        active = await self._roster.list_available()
        # Only a planned initiative (a greenlit objective / charter) is
        # staffed with an accountable owner. A one-off leaf task landing in an
        # existing project must never hijack that project's lead.
        if not work_item.plan_required:
            return None, active
        async with self._project_locks.acquire(work_item.project):
            owner = await self._staff_owner_locked(work_item, task, active)
        return owner, active

    async def _staff_owner_locked(
        self,
        work_item: WorkItem,
        task: Task,
        active: tuple[AgentIdentity, ...],
    ) -> AgentIdentity | None:
        """Resolve or stamp the project's owner while holding the project lock.

        The in-process lock serialises same-process runs, but a second worker
        process shares only the database, so the lead stamp is written under
        optimistic concurrency: the version-guarded write loses to a concurrent
        stamp rather than clobbering it, and a lost write re-reads to return the
        winner's lead (bounded by ``_MAX_STAFF_ATTEMPTS``). An already-led
        project resolves its lead via the registry regardless of status, so a
        paused or offboarded lead is surfaced as an orphan rather than dropped.

        Returns:
            The owning :class:`AgentIdentity`, or ``None`` when the project
            vanished mid-flight, the durable lead no longer resolves or is
            unavailable, the roster is empty, or the selector abstains.
        """
        for attempt in range(1, _MAX_STAFF_ATTEMPTS + 1):
            project = await self._project_repository.get(work_item.project)
            if project is None:
                return None
            if project.lead is not None:
                owner = await self._roster.get(project.lead)
                if owner is None:
                    logger.warning(
                        PIPELINE_PROJECT_LEAD_ORPHANED,
                        project=work_item.project,
                        lead=project.lead,
                    )
                    return None
                # ``get`` answers regardless of availability, deliberately, so
                # an offboarded lead surfaces rather than vanishing. Running
                # planning as a lead whose pair cannot serve is a different
                # thing: the work would execute as an employee who is out.
                if owner.id not in {agent.id for agent in active}:
                    logger.warning(
                        PIPELINE_PROJECT_LEAD_UNAVAILABLE,
                        project=work_item.project,
                        lead=project.lead,
                    )
                    return None
                return owner
            if not active:
                logger.warning(
                    PIPELINE_PROJECT_ROSTER_EMPTY,
                    project=work_item.project,
                )
                return None
            owner = select_project_owner(task, active, scorer=self._scorer)
            if owner is None:
                return None
            try:
                await self._project_repository.update(
                    project.model_copy(
                        update={
                            "lead": str(owner.id),
                            "version": project.version + 1,
                        }
                    ),
                    expected_version=project.version,
                )
            except PersistenceVersionConflictError:
                # A concurrent process stamped a lead first; re-read to return
                # the winner rather than clobbering it (never raise off this
                # best-effort staffing path).
                logger.info(
                    PIPELINE_PROJECT_LEAD_CONTENDED,
                    project=work_item.project,
                    attempt=attempt,
                )
                continue
            logger.info(
                PIPELINE_PROJECT_LEAD_STAMPED,
                project=work_item.project,
                lead=str(owner.id),
            )
            return owner
        return None

    async def _decompose(
        self,
        task: Task,
        agents: tuple[AgentIdentity, ...],
    ) -> tuple[RoutingVerdict, tuple[AgentIdentity, ...]]:
        """Decide solo-vs-team over the run's active-agent roster.

        The roster is resolved once in the project phase and threaded in, so
        the routing decision reuses it rather than issuing a second registry
        read per run.

        Returns:
            ``(verdict, agents)`` where ``verdict`` is the solo-vs-team
            decision and ``agents`` is the roster passed to the routing policy.
        """
        verdict = await self._routing_policy.decide(task=task, available_agents=agents)
        return verdict, agents

    def _should_refine(self, task: Task) -> bool:
        """Decide whether team-bound *task* must be refined before dispatch.

        Team work needs a definition of done: the coordinator's
        clarification gate blocks decomposition of a task with no
        acceptance criteria. When a refinement router is wired, the spine
        opens a human-in-the-loop refinement conversation instead of
        letting the gate raise. Absent a router (e.g. the Chief of Staff is
        off), the work falls through to the coordinator and the gate blocks
        it -- the honest "this needs a definition of done" signal.

        Returns:
            ``True`` when a router is wired and the task carries no
            acceptance criteria; ``False`` otherwise.
        """
        return self._refinement_router is not None and not task.acceptance_criteria

    async def _refine(self, work_item: WorkItem, task: Task) -> RefinementHandoff:
        """Hand under-specified team work to human-in-the-loop refinement.

        Returns:
            The :class:`RefinementHandoff` the caller surfaces so the
            human can continue the refinement conversation.
        """
        router = self._refinement_router
        assert router is not None  # noqa: S101 -- guarded by _should_refine
        reasons = ("no acceptance criteria defined",)
        handoff = await router.request_refinement(
            work_item=work_item,
            task=task,
            reasons=reasons,
        )
        logger.info(
            PIPELINE_REFINEMENT_REQUESTED,
            correlation_id=work_item.correlation_id,
            task_id=str(task.id),
            conversation_id=handoff.conversation_id,
            needs_clarification=handoff.needs_clarification,
        )
        return handoff

    def _should_gate_plan(self) -> bool:
        """Whether splittable team work is gated on human plan approval.

        The gate applies only when both a plan-review gate and a coordinator
        are wired: the gate needs the coordinator to decompose the plan, and
        without the gate the work dispatches straight to the team.

        Returns:
            ``True`` when the plan-approval gate should run instead of
            immediate team dispatch.
        """
        return self._plan_review_gate is not None and self._coordinator is not None

    async def _plan_review(
        self,
        work_item: WorkItem,
        task: Task,
        agents: tuple[AgentIdentity, ...],
        phases: list[WorkPhaseResult],
        owner: AgentIdentity | None,
    ) -> PlanReviewHandoff:
        """Decompose the plan, run the stakeholder panel, and park it.

        Runs the decompose-only half of coordination to produce the subtask
        tree, then (when a panel is attached) runs a bounded stakeholder review
        as a distinct phase and attaches the consolidated verdict to the durable
        plan the gate persists. Nothing is dispatched; the approved plan is
        dispatched verbatim on approval.

        Returns:
            The :class:`PlanReviewHandoff` the caller surfaces so the human
            can approve the plan. If any step (decomposition, the panel, or
            parking the approval) fails, the handoff carries ``approval_id=None``,
            the durable plan is marked FAILED and stays visible in Plan Review,
            the root task is marked FAILED, and nothing is dispatched: no 500
            escapes and no orphan is left.
        """
        coordinator = self._coordinator
        gate = self._plan_review_gate
        assert coordinator is not None  # noqa: S101 -- guarded by _should_gate_plan
        assert gate is not None  # noqa: S101 -- guarded by _should_gate_plan

        async def build_plan(planned: Task) -> DecompositionResult:
            return await coordinator.plan_preview(
                self._coordination_context(planned, agents, owner)
            )

        async def review_plan(
            round_index: int,
            planned: Task,
            plan: DecompositionResult,
        ) -> PlanReviewOutcome:
            return await self._phase(
                phases,
                review_phase_name(round_index),
                self._run_review_panel(planned, plan, agents, owner),
            )

        # Persist the plan as a first-class shell at greenlight, so a failure
        # anywhere below leaves a visible FAILED plan rather than an orphan task.
        plan_id = await gate.open_plan(work_item=work_item, task=task)
        try:
            reviewed = await build_reviewed_plan(
                task=task,
                build_plan=build_plan,
                review_plan=review_plan,
                max_rounds=self._max_revision_rounds(),
            )
            # The durable plan is for the objective the operator filed, not for
            # the briefed copy a revision round planned against: the brief is a
            # planning input that dies with the round.
            handoff = await gate.request_plan_approval(
                plan_id=plan_id,
                work_item=work_item,
                task=task,
                plan=reviewed.plan,
                review=reviewed.outcome,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- not swallowed: any failure across
            # decomposition, the review panel, or parking the approval is
            # surfaced as a FAILED plan + FAILED task + is_success=false result,
            # so the greenlit run never emits a 500 nor leaves a silent orphan.
            reraise_critical(exc)
            return await self._compensate_plan_failure(plan_id, task, work_item, exc)
        finally:
            # The attempt is over on every route out of here, including the
            # ones no handler above sees: a cancellation is not an Exception,
            # and a re-raised critical leaves from inside the handler. Both
            # skip the gate's own two releases, and a claim that outlives its
            # attempt is skipped by the recovery sweep for the life of the
            # process, so the plan it names is stranded by the mechanism that
            # exists to revive it. Idempotent, so this is a no-op wherever the
            # gate has already released.
            gate.release_plan(plan_id)
        logger.info(
            PIPELINE_PLAN_REVIEW_REQUESTED,
            correlation_id=work_item.correlation_id,
            task_id=str(task.id),
            approval_id=handoff.approval_id,
            subtask_count=handoff.subtask_count,
            revision_rounds=reviewed.rounds_used,
            review_settled=reviewed.settled,
        )
        return handoff

    def _max_revision_rounds(self) -> int:
        """How many revision rounds the attached panel's findings may drive.

        Returns:
            The panel's own cap, or zero when no panel is attached: nothing
            raised a finding, so there is nothing to re-plan against.
        """
        panel = self._plan_review_panel
        return 0 if panel is None else panel.max_revision_rounds

    async def _compensate_plan_failure(
        self,
        plan_id: UUID,
        task: Task,
        work_item: WorkItem,
        exc: Exception,
    ) -> PlanReviewHandoff:
        """Mark the plan + root task FAILED and return a failed handoff.

        Shared by every failure in the plan-review sequence (decomposition, the
        stakeholder panel, or parking the approval). Both compensating writes are
        best-effort (``fail_plan`` / ``_fail_task`` never raise), so the greenlit
        run always yields a visible FAILED plan instead of an orphan, and never a
        500.

        Returns:
            A :class:`PlanReviewHandoff` with ``approval_id=None`` naming the
            FAILED plan the operator can inspect and re-run.
        """
        gate = self._plan_review_gate
        assert gate is not None  # noqa: S101 -- guarded by _should_gate_plan
        reason = safe_error_description(exc)
        logger.warning(
            PIPELINE_PLAN_DECOMPOSITION_FAILED,
            correlation_id=work_item.correlation_id,
            task_id=str(task.id),
            plan_id=str(plan_id),
            error_type=type(exc).__name__,
            error=reason,
        )
        await gate.fail_plan(plan_id=plan_id, reason=reason)
        await self._fail_task(task, work_item, reason=reason)
        return PlanReviewHandoff(
            approval_id=None,
            plan_id=NotBlankStr(str(plan_id)),
            subtask_count=0,
            detail=NotBlankStr(f"Plan preparation failed: {reason}"),
        )

    async def _fail_task(self, task: Task, work_item: WorkItem, *, reason: str) -> None:
        """Transition the objective's root task to FAILED, best-effort.

        A plan-review failure (decomposition, panel, or parking) marks the root
        task FAILED so it surfaces on the board and stays re-runnable. A
        transition hiccup is logged, not raised: the run is already reported
        unsuccessful (final status FAILED) and the durable plan is FAILED, so a
        failed status-write must not mask the original failure or turn it into a
        500.
        """
        try:
            await self._task_engine.transition_task(
                str(task.id),
                TaskStatus.FAILED,
                requested_by=work_item.requested_by,
                reason=f"plan preparation failed: {reason}",
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- best-effort FAILED transition; the run is
            # already reported unsuccessful and the durable plan is FAILED, so a
            # status-write hiccup must not mask the decomposition failure.
            reraise_critical(exc)
            logger.warning(
                PIPELINE_PLAN_FAIL_TRANSITION_FAILED,
                correlation_id=work_item.correlation_id,
                task_id=str(task.id),
                note="failed to transition root task to FAILED",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def _run_review_panel(
        self,
        task: Task,
        plan: DecompositionResult,
        agents: tuple[AgentIdentity, ...],
        owner: AgentIdentity | None,
    ) -> PlanReviewOutcome:
        """Run the stakeholder panel over *plan* when one is attached.

        Returns:
            The panel's outcome, or an outcome naming "no panel attached"
            when the operator wired none. Either way the durable plan can
            say why it carries no review.

        Raises:
            Exception: A panel that errors mid-review propagates so the caller's
                plan-review guard compensates it (FAILED plan + FAILED task),
                rather than parking a plan whose holistic review silently never
                ran.
        """
        panel = self._plan_review_panel
        if panel is None:
            return PlanReviewOutcome(absent_reason=NotBlankStr(_NO_PANEL_ATTACHED))
        try:
            return await panel.review(task=task, plan=plan, agents=agents, owner=owner)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                PIPELINE_PLAN_REVIEW_PANEL_FAILED,
                task_id=str(task.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise

    async def _run_solo(
        self,
        work_item: WorkItem,
        task: Task,
        agents: tuple[AgentIdentity, ...],
    ) -> TaskStatus:
        """Assign one agent and execute the leaf task single-agent.

        Returns:
            The post-execution :class:`TaskStatus` reported by the
            worker execution service.
        """
        if not task.assigned_to:
            assigned_id = select_solo_agent(
                task,
                agents,
                scorer=self._scorer,
                assignment_service=self._assignment_service,
            )
            await self._task_engine.transition_task(
                str(task.id),
                TaskStatus.ASSIGNED,
                requested_by=work_item.requested_by,
                reason="work pipeline solo routing",
                assigned_to=assigned_id,
            )
        post = await self._worker_execution_service.execute_once(
            task_id=str(task.id),
            previous_status=TaskStatus.ASSIGNED.value,
            new_status=TaskStatus.IN_PROGRESS.value,
            idempotency_key=work_item.correlation_id,
            requested_by=work_item.requested_by,
        )
        return post.status

    async def _run_team(
        self,
        work_item: WorkItem,
        task: Task,
        agents: tuple[AgentIdentity, ...],
        owner: AgentIdentity | None,
    ) -> TaskStatus:
        """Hand splittable work to the multi-agent coordinator.

        Returns:
            The post-coordination :class:`TaskStatus` read from the
            task engine after the coordinator returns.

        Raises:
            WorkPipelineTeamPathUnavailableError: If no coordinator
                is wired (empty-company boot).
            WorkRoutingUndecidableError: If ``agents`` is empty.
            WorkPipelineError: If the task is missing from the task
                engine after coordination.
        """
        del work_item
        if self._coordinator is None:
            logger.warning(
                PIPELINE_TEAM_PATH_UNAVAILABLE,
                task_id=str(task.id),
                error_type=WorkPipelineTeamPathUnavailableError.__name__,
            )
            raise WorkPipelineTeamPathUnavailableError
        if not agents:
            msg = "no active agents available for team coordination"
            logger.warning(
                PIPELINE_ROUTING_UNDECIDABLE,
                task_id=str(task.id),
                reason="no_active_agents",
                path="team",
                error_type=WorkRoutingUndecidableError.__name__,
            )
            raise WorkRoutingUndecidableError(msg)
        await self._coordinator.coordinate(
            self._coordination_context(task, agents, owner)
        )
        post = await self._task_engine.get_task(str(task.id))
        if post is None:
            msg = f"task {str(task.id)!r} missing after coordination"
            logger.warning(
                PIPELINE_TASK_MISSING,
                task_id=str(task.id),
                phase="post_coordination",
                error_type=WorkPipelineError.__name__,
            )
            raise WorkPipelineError(msg)
        return post.status

    async def _metrics_stage(self) -> None:
        """Record the coordination-metrics stage.

        Metrics records are written by the shared
        ``CoordinationMetricsCollector`` already threaded into the boot
        ``AgentEngine`` (solo) and the coordinator (team); the spine
        owns no separate collection to avoid a second, divergent
        source of truth.
        """
        return
