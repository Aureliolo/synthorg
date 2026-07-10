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
from typing import TYPE_CHECKING, TypeVar

from synthorg.client.models import ClientRequest, TaskRequirement
from synthorg.core.agent import AgentIdentity
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.engine.assignment.service import TaskAssignmentService
from synthorg.engine.coordination.models import CoordinationContext
from synthorg.engine.coordination.service import MultiAgentCoordinator
from synthorg.engine.errors import ProjectNotFoundError
from synthorg.engine.intake.engine import IntakeEngine
from synthorg.engine.pipeline._solo_selection import select_solo_agent
from synthorg.engine.pipeline.errors import (
    WorkIntakeRejectedError,
    WorkPipelineError,
    WorkPipelineTeamPathUnavailableError,
    WorkRoutingUndecidableError,
)
from synthorg.engine.pipeline.models import (
    ExecutionPath,
    PlanReviewHandoff,
    RefinementHandoff,
    RoutingVerdict,
    WorkItem,
    WorkPhaseResult,
    WorkPipelineResult,
    WorkSource,
)
from synthorg.engine.pipeline.narrator_port import RunNarrator
from synthorg.engine.pipeline.plan_review_port import PlanReviewGate
from synthorg.engine.pipeline.policy.protocol import WorkRoutingPolicy
from synthorg.engine.pipeline.refinement_port import WorkRefinementRouter
from synthorg.engine.routing.scorer import AgentTaskScorer
from synthorg.engine.stakes import build_stakes_assessor
from synthorg.engine.stakes.protocol import StakesAssessor
from synthorg.engine.task_engine import TaskEngine
from synthorg.hr.registry_protocol import AgentRegistryProtocol
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.chief_of_staff import (
    COS_NARRATIVE_GENERATION_FAILED,
    COS_NARRATIVE_SKIPPED,
)
from synthorg.observability.events.pipeline import (
    PIPELINE_PHASE_COMPLETED,
    PIPELINE_PHASE_FAILED,
    PIPELINE_PLAN_REVIEW_REQUESTED,
    PIPELINE_PROJECT_NOT_FOUND,
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

#: Work sources whose ``requested_by`` is a human user id (not an agent or
#: system identity). Only these stamp ``Task.requested_by_user_id`` so the SSE
#: event-stream endpoint can resolve session ownership; agent/system sources
#: leave it ``None`` (no human owner, so only a CEO may stream them).
_HUMAN_ORIGINATED_SOURCES: frozenset[WorkSource] = frozenset(
    {WorkSource.TASK_BOARD, WorkSource.CONVERSATIONAL}
)


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
        agent_registry: Active-agent pool source.
        clock: Injectable time source (defaults to the system clock).
    """

    __slots__ = (
        "_agent_registry",
        "_assignment_service",
        "_clock",
        "_coordinator",
        "_intake_engine",
        "_narrator",
        "_plan_review_gate",
        "_project_repository",
        "_refinement_router",
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
        agent_registry: AgentRegistryProtocol,
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
        self._agent_registry = agent_registry
        self._clock = clock if clock is not None else SystemClock()
        self._stakes_assessor = stakes_assessor or build_stakes_assessor()
        # Solo-path assignment service: when wired, the single-agent
        # pick routes through ``TaskAssignmentService`` so its status
        # validation and project-team filter run in production. Absent
        # -> the direct-scorer fallback below preserves prior behaviour.
        self._assignment_service = assignment_service
        self._narrator: RunNarrator | None = None
        self._refinement_router: WorkRefinementRouter | None = None
        self._plan_review_gate: PlanReviewGate | None = None

    def attach_narrator(self, narrator: RunNarrator) -> None:
        """Attach the post-run narrator (documentary mode).

        Late-bind seam: the narrator depends on services that wire only
        after persistence connects, so it is attached to the already-built
        pipeline by the startup hook rather than passed at construction.
        """
        self._narrator = narrator

    def attach_refinement_router(self, router: WorkRefinementRouter) -> None:
        """Attach the under-specified-team-work refinement router.

        Late-bind seam: the router wraps the Chief-of-Staff proposer,
        which wires only after persistence and a provider are available,
        so it is attached to the already-built pipeline by the startup
        hook. Absent, team-bound work with no definition of done falls
        through to the coordinator, where the clarification gate blocks it.
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
        """
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
            await self._phase(phases, _PHASE_PROJECTS, self._resolve_project(work_item))
            verdict, agents = await self._phase(
                phases, _PHASE_DECOMPOSE, self._decompose(task)
            )
            refinement_handoff: RefinementHandoff | None = None
            plan_review_handoff: PlanReviewHandoff | None = None
            if verdict is RoutingVerdict.LEAF:
                path = ExecutionPath.SOLO
                final_status = await self._phase(
                    phases, _PHASE_SOLO, self._run_solo(work_item, task, agents)
                )
                await self._phase(phases, _PHASE_METRICS, self._metrics_stage())
            elif self._should_refine(task):
                # Team-bound work with no definition of done: refine with a
                # human before mobilising a team (the clarification gate
                # would otherwise block decomposition). No coordination
                # runs, so the coordination-metrics stage is skipped.
                path = ExecutionPath.REFINEMENT
                refinement_handoff = await self._phase(
                    phases, _PHASE_REFINE, self._refine(work_item, task)
                )
                final_status = task.status
            elif self._should_gate_plan():
                # Splittable team work under a human plan-approval gate:
                # decompose into a plan, park it for approval, and stop.
                # Nothing builds until the plan is approved, at which point
                # the approved plan is dispatched verbatim.
                path = ExecutionPath.PLAN_REVIEW
                plan_review_handoff = await self._phase(
                    phases,
                    _PHASE_PLAN_REVIEW,
                    self._plan_review(work_item, task, agents),
                )
                final_status = task.status
            else:
                path = ExecutionPath.TEAM
                final_status = await self._phase(
                    phases, _PHASE_TEAM, self._run_team(work_item, task, agents)
                )
                await self._phase(phases, _PHASE_METRICS, self._metrics_stage())
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
            execution_path=path,
            task_id=str(task.id),
            final_task_status=final_status,
            phases=tuple(phases),
            refinement_handoff=refinement_handoff,
            plan_review_handoff=plan_review_handoff,
            total_duration_seconds=total,
        )
        logger.info(
            PIPELINE_RUN_COMPLETED,
            correlation_id=work_item.correlation_id,
            task_id=str(task.id),
            verdict=verdict.value,
            execution_path=path.value,
            final_task_status=final_status.value,
            total_duration_seconds=total,
        )
        await self._try_generate_narrative(work_item, task)
        return result

    async def _try_generate_narrative(self, work_item: WorkItem, task: Task) -> None:
        """Generate the run narrative, best-effort.

        Documentary mode is opt-in and never blocks or fails the run: a
        missing narrator is a no-op, and any error degrades to a logged
        warning. Critical interpreter errors still propagate.
        """
        # Snapshot once: the attach is a monotonic None -> narrator late-bind,
        # so a single load keeps the null-check and the call on the same value.
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

    async def _resolve_project(self, work_item: WorkItem) -> None:
        """Bind the work to its project context (existence check).

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

    async def _decompose(
        self,
        task: Task,
    ) -> tuple[RoutingVerdict, tuple[AgentIdentity, ...]]:
        """Fetch the active-agent pool and decide solo-vs-team.

        Both the agent-registry lookup and the routing decision run
        inside the decompose phase so registry errors and lookup
        latency are captured by the phase telemetry.

        Returns:
            ``(verdict, agents)`` where ``verdict`` is the
            solo-vs-team decision and ``agents`` is the tuple of
            active agents passed to the routing policy.
        """
        agents = await self._agent_registry.list_active()
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
    ) -> PlanReviewHandoff:
        """Decompose the plan and park it for human approval.

        Runs the decompose-only half of coordination to produce the subtask
        tree, then hands it to the plan-review gate, which persists the plan
        and parks a plan-approval item. Nothing is dispatched; the approved
        plan is dispatched verbatim on approval.

        Returns:
            The :class:`PlanReviewHandoff` the caller surfaces so the human
            can approve the plan.
        """
        coordinator = self._coordinator
        gate = self._plan_review_gate
        assert coordinator is not None  # noqa: S101 -- guarded by _should_gate_plan
        assert gate is not None  # noqa: S101 -- guarded by _should_gate_plan
        plan = await coordinator.plan_preview(
            CoordinationContext(task=task, available_agents=agents)
        )
        handoff = await gate.request_plan_approval(
            work_item=work_item,
            task=task,
            plan=plan,
        )
        logger.info(
            PIPELINE_PLAN_REVIEW_REQUESTED,
            correlation_id=work_item.correlation_id,
            task_id=str(task.id),
            approval_id=handoff.approval_id,
            subtask_count=handoff.subtask_count,
        )
        return handoff

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
            CoordinationContext(task=task, available_agents=agents)
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
