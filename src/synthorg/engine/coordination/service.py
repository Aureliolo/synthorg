# module-kind: complex_service
"""Multi-agent coordination service.

Orchestrates: decompose, route, resolve topology, dispatch, rollup,
update parent. Rollup + parent lifecycle walk live in
:mod:`synthorg.engine.coordination.parent_rollup`.
"""

import asyncio
from collections.abc import (
    Callable,
)
from pathlib import Path
from typing import TYPE_CHECKING, Final, NamedTuple
from uuid import uuid4

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task_enums import (
    UNROUTABLE_ROLE_KEY,
    BlockedReason,
    CoordinationTopology,
    TaskStatus,
)
from synthorg.core.types import NotBlankStr
from synthorg.engine.coordination._completion import (
    aggregate_wave_cost,
    record_contributions,
)
from synthorg.engine.coordination._dependency_gate import awaits_dispatch
from synthorg.engine.coordination._middleware_relay import (
    CoordinationMiddlewareRelay,
)
from synthorg.engine.coordination._phase_recorder import (
    begin_phase,
    record_phase_failure,
    record_phase_success,
)
from synthorg.engine.coordination._topology_phase import resolve_topology_phase
from synthorg.engine.coordination.assignment_writer import AssignmentWriter
from synthorg.engine.coordination.attribution import (
    CoordinationResultWithAttribution,
)
from synthorg.engine.coordination.dispatcher_factory import select_dispatcher
from synthorg.engine.coordination.dispatcher_types import DispatchResult
from synthorg.engine.coordination.metrics import collect_coordination_metrics
from synthorg.engine.coordination.models import (
    CoordinationContext,
    CoordinationPhaseResult,
    CoordinationResult,
)
from synthorg.engine.coordination.parent_rollup import (
    compute_status_rollup,
    run_update_parent_phase,
)
from synthorg.engine.decomposition.models import (
    DecompositionResult,
)
from synthorg.engine.errors import (
    CoordinationPhaseError,
    DelegationRoundLimitError,
)
from synthorg.engine.parallel_protocol import ParallelExecutorProtocol
from synthorg.engine.routing.models import RoutingResult
from synthorg.engine.task_engine_models import TransitionTaskMutation
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.async_task import (
    DELEGATION_ROUND_HARD_LIMIT,
    DELEGATION_ROUND_SOFT_LIMIT,
)
from synthorg.observability.events.coordination import (
    COORDINATION_COMPLETED,
    COORDINATION_FAILED,
    COORDINATION_PHASE_COMPLETED,
    COORDINATION_PHASE_FAILED,
    COORDINATION_STARTED,
    COORDINATION_TOPOLOGY_RESOLVED,
    COORDINATION_UNROUTABLE_PARK_FAILED,
    COORDINATION_UNROUTABLE_PARKED,
)

if TYPE_CHECKING:
    # Cold-import cycle-breakers: importing these collaborators at module
    # level pulls the routing / decomposition / workspace chain (which reaches
    # ``communication.config``) back through this module during a cold import,
    # so they are named for signatures only. Tests inject duck-typed fakes
    # against the same surface. ``ParallelExecutorProtocol`` (a light leaf) is
    # the hoistable structural view of the parallel executor.
    from synthorg.budget.coordination_collector import (
        CoordinationMetricsCollector,
    )
    from synthorg.engine.decomposition.service import DecompositionService
    from synthorg.engine.middleware.coordination_protocol import (
        CoordinationMiddlewareChain,
    )
    from synthorg.engine.routing.service import TaskRoutingService
    from synthorg.engine.task_engine import TaskEngine
    from synthorg.engine.workspace.project_workspace_service import (
        ProjectWorkspaceService,
    )
    from synthorg.engine.workspace.service import WorkspaceIsolationService
    from synthorg.hr.performance.tracker import PerformanceTracker

logger = get_logger(__name__)

#: Recorded as the writer on an unroutable subtask's park, matching the
#: assignment writer's actor so the two coordination-owned writes read as one
#: component in the ledger.
_UNROUTABLE_ACTOR: Final[str] = "coordinator"

_UNROUTABLE_REASON: Final[str] = (
    "No agent could take this subtask: none the stakes admit, at any "
    "capability rung, matched what it asks for"
)


class _PipelineRun(NamedTuple):
    """What the phases produced, for the tail that reports on them.

    The routing and the dispatch are both on it because the assembled
    result carries neither in the shape the tail needs: its own
    ``routing_result`` is optional (a result can be built without one) and
    it holds the waves rather than the dispatch they came from.

    Attributes:
        result: The assembled coordination result.
        routing_result: Who was routed what, for attribution.
        dispatch_result: What the waves produced, for the metrics collector.
    """

    result: CoordinationResult
    routing_result: RoutingResult
    dispatch_result: DispatchResult


class MultiAgentCoordinator:
    """Orchestrates multi-agent task execution.

    Composes existing engine services (decomposition, routing,
    parallel execution, workspace isolation, task engine) into
    an end-to-end coordination pipeline.

    The coordinator is available both as a peer service (via
    ``AppState``) and as an optional dependency of ``AgentEngine``
    (which exposes a ``coordinate()`` convenience method). It
    operates at a higher level, composing existing services via
    dependency injection.

    Args:
        decomposition_service: Service to decompose tasks into subtasks.
        routing_service: Service to route subtasks to agents.
        parallel_executor: Executor for parallel agent runs.
        workspace_service: Optional workspace isolation service.
        project_workspace_service: Optional per-project workspace
            provisioner. When supplied alongside ``workspace_service``,
            the dispatch merge step routes through the per-project
            push queue (``merge_workspace_with_push``) so forge-collision
            safety runs end to end; otherwise the merge falls back to
            the in-memory ``merge_group``.
        task_engine: Optional task engine for parent status updates.
        performance_tracker: Optional tracker for recording per-agent
            coordination contributions.
        coordination_chain: Optional ``CoordinationMiddlewareChain``
            that runs ``before_decompose`` / ``after_decompose`` /
            ``before_dispatch`` / ``after_rollup`` /
            ``before_update_parent`` hooks around the pipeline.
            ``coordinate()`` invokes each hook via the corresponding
            ``run_*`` method on the chain, so middleware implementers
            must define all five hooks (though each may be a no-op).
            ``None`` disables middleware entirely.
        default_topology_provider: Optional callable returning the
            topology to fall back on when ``routing_result.decisions``
            is empty. Passed as a callable (rather than a frozen
            :class:`CoordinationTopology`) so operators can wire it
            to a settings-store reader and runtime changes to
            ``coordination.default_topology`` take effect without
            rebuilding the coordinator. Falls back to
            ``CoordinationTopology.SAS`` (the historical default)
            when ``None`` is supplied.
        coordination_metrics_collector: Optional collector invoked
            post-completion to compute and record the multi-agent
            coordination metrics. ``None`` disables collection (never
            fatal: a collector failure cannot fail a completed run).
    """

    __slots__ = (
        "_clock",
        "_coordination_chain",
        "_coordination_metrics_collector",
        "_decomposition_service",
        "_default_topology_provider",
        "_parallel_executor",
        "_performance_tracker",
        "_project_workspace_service",
        "_routing_service",
        "_task_engine",
        "_workspace_service",
    )

    def __init__(  # noqa: PLR0913
        self,
        *,
        decomposition_service: DecompositionService,
        routing_service: TaskRoutingService,
        parallel_executor: ParallelExecutorProtocol,
        workspace_service: WorkspaceIsolationService | None = None,
        project_workspace_service: ProjectWorkspaceService | None = None,
        task_engine: TaskEngine | None = None,
        performance_tracker: PerformanceTracker | None = None,
        coordination_chain: CoordinationMiddlewareChain | None = None,
        default_topology_provider: Callable[[], CoordinationTopology] | None = None,
        clock: Clock | None = None,
        coordination_metrics_collector: CoordinationMetricsCollector | None = None,
    ) -> None:
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._coordination_metrics_collector = coordination_metrics_collector
        self._decomposition_service = decomposition_service
        self._routing_service = routing_service
        self._parallel_executor = parallel_executor
        self._workspace_service = workspace_service
        self._project_workspace_service = project_workspace_service
        self._task_engine = task_engine
        self._performance_tracker = performance_tracker
        self._coordination_chain = coordination_chain
        # Callable provider instead of a frozen value so operators
        # can wire a settings-store reader here and runtime changes
        # to ``coordination.default_topology`` take effect without
        # rebuilding the coordinator. Falls back to ``SAS`` (the
        # historical default) when no provider is supplied.
        self._default_topology_provider = default_topology_provider

    @property
    def decomposition_service(self) -> DecompositionService:
        """The planner this coordinator decomposes objectives with.

        Exposed so a caller that needs a plan rather than a coordinated run
        (the auto-replan trigger, which re-decomposes a stalled objective)
        reuses the same wired planner instead of constructing a second one
        that could drift from it.

        Returns:
            The wired :class:`DecompositionService`.
        """
        return self._decomposition_service

    def _enforce_delegation_rounds(self, context: CoordinationContext) -> None:
        """Guard against runaway recursive delegation before coordinating.

        The parent task's ``delegation_chain`` depth is the number of
        delegation hops that led to this coordination. A soft warning is
        emitted once that depth reaches ``max_delegation_rounds``; the run
        hard-aborts once it reaches twice the cap. Complements the per-
        delegation ``loop_prevention`` depth guard with a coordination-
        side ceiling.

        Raises:
            DelegationRoundLimitError: When the delegation depth reaches
                twice the configured soft cap.
        """
        rounds = len(context.task.delegation_chain)
        soft_limit = context.config.max_delegation_rounds
        if rounds >= soft_limit * 2:
            logger.warning(
                DELEGATION_ROUND_HARD_LIMIT,
                parent_task_id=str(context.task.id),
                delegation_rounds=rounds,
                soft_limit=soft_limit,
                hard_limit=soft_limit * 2,
            )
            raise DelegationRoundLimitError(rounds, soft_limit)
        if rounds >= soft_limit:
            logger.warning(
                DELEGATION_ROUND_SOFT_LIMIT,
                parent_task_id=str(context.task.id),
                delegation_rounds=rounds,
                soft_limit=soft_limit,
            )

    async def plan_preview(
        self,
        context: CoordinationContext,
    ) -> DecompositionResult:
        """Decompose the task into a plan WITHOUT routing or dispatching.

        The decompose-only half of the pipeline: it produces the subtask
        tree a human plan-approval gate surfaces for review before any agent
        builds. Nothing is routed, dispatched, or persisted to the task tree;
        the returned :class:`DecompositionResult` is the durable plan the
        gate serialises and later feeds back to :meth:`coordinate` as
        ``precomputed_plan`` so the built plan is exactly the approved one.

        Returns:
            The :class:`DecompositionResult` for the parent task.

        Raises:
            CoordinationPhaseError: When decomposition fails.
        """
        phases: list[CoordinationPhaseResult] = []
        return await self._phase_decompose(context, phases)

    async def coordinate(
        self,
        context: CoordinationContext,
        *,
        precomputed_plan: DecompositionResult | None = None,
    ) -> CoordinationResultWithAttribution:
        """Run the full multi-agent coordination pipeline.

        Pipeline:
            1. Decompose task into subtasks via DecompositionService.
            2. Route subtasks to agents via TaskRoutingService.
            3. Resolve topology from routing decisions.
            4. Validate: fail if ALL subtasks are unroutable.
            5. Select dispatcher and execute waves.
            6. Rollup subtask statuses.
            7. Update parent task via TaskEngine (if provided).
            8. Build per-agent attribution from routing + outcomes.

        Args:
            context: Coordination context with task, agents, and config.
            precomputed_plan: An already-approved :class:`DecompositionResult`
                (from :meth:`plan_preview`) to dispatch instead of
                re-decomposing. Supplied by the plan-approval gate on resume
                so the built plan matches exactly what the human approved; it
                also marks the plan-review gate as satisfied so dispatch is
                not re-gated. ``None`` runs the normal decompose-then-dispatch
                flow.

        Returns:
            CoordinationResultWithAttribution wrapping the result
            with per-agent contribution data.

        Raises:
            CoordinationPhaseError: When a critical phase fails.
        """
        task = context.task
        logger.info(
            COORDINATION_STARTED,
            parent_task_id=str(task.id),
            agent_count=len(context.available_agents),
        )
        self._enforce_delegation_rounds(context)

        try:
            ran = await self._run_pipeline(context, precomputed_plan)
        except CoordinationPhaseError:
            # Already logged and already carrying its partial phase list;
            # re-logging here would report one failure twice.
            raise
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                COORDINATION_FAILED,
                parent_task_id=str(task.id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise

        logger.info(
            COORDINATION_COMPLETED,
            parent_task_id=str(task.id),
            topology=ran.result.topology.value,
            is_success=ran.result.is_success,
            total_duration_seconds=ran.result.total_duration_seconds,
            total_cost=ran.result.total_cost,
        )
        contributions = await record_contributions(
            ran.routing_result,
            ran.dispatch_result,
            performance_tracker=self._performance_tracker,
            parent_task_id=str(task.id),
        )
        await collect_coordination_metrics(
            self._coordination_metrics_collector,
            task_id=str(task.id),
            dispatch_result=ran.dispatch_result,
        )
        return CoordinationResultWithAttribution(
            result=ran.result,
            agent_contributions=contributions,
        )

    async def _run_pipeline(
        self,
        context: CoordinationContext,
        precomputed_plan: DecompositionResult | None,
    ) -> _PipelineRun:
        """Run the phases, in the order they have to be run in.

        Everything here can fail the run, which is what separates it from
        the reporting either side: the caller announces the run, decides
        what a failure means, and then records what happened.

        Returns:
            What the phases produced, for the reporting tail.
        """
        pipeline_start = self._clock.monotonic()
        phases: list[CoordinationPhaseResult] = []
        relay = CoordinationMiddlewareRelay(self._coordination_chain)

        await relay.opened(context, plan_preapproved=precomputed_plan is not None)
        decomp_result = await self._decompose_or_reuse(
            context, precomputed_plan, phases
        )
        decomp_result = await relay.after_decompose(decomp_result, phases)

        # Filed here rather than inside the decompose phase, which
        # ``plan_preview`` also runs: a preview must leave no task rows
        # behind for work a human has not approved. Both decompose
        # branches reach this, so an approved-plan resume files its
        # children too. After the middleware rather than before, because
        # a replaced result is the tree that actually dispatches, and
        # filing the superseded one would leave the wave assigning
        # subtasks with no row.
        await self._phase_file_children(decomp_result, phases)

        routing_result = self._phase_route(context, decomp_result, phases)
        # Before validation and topology resolution, so a routing the
        # middleware mutated (re-routing an unassigned subtask, enriching
        # topology metadata) is what both of them consume.
        routing_result = await relay.before_dispatch(routing_result, phases)

        # Park what routing could not place, BEFORE dispatch: from here on
        # nothing else in the pipeline looks at an unroutable subtask, so
        # a row left CREATED is a row nobody will ever move again.
        #
        # And before the validation below, not after it. That raises when
        # EVERY subtask is unroutable, which is the case with the most
        # rows to strand, so parking afterwards fixed the partial case and
        # left the total one exactly as it was: N children filed, none
        # assigned, none carrying a reason, on a board reporting the plan
        # as executing.
        await self._park_unroutable(routing_result, decomp_result)

        # Fail fast if all subtasks are unroutable, BEFORE resolving
        # topology, so the deterministic routing error surfaces without
        # first calling ``default_topology_provider()`` (which may read
        # runtime settings or raise).
        self._validate_routing(routing_result, phases)
        topology = resolve_topology_phase(
            self._resolve_topology, routing_result, phases, clock=self._clock
        )

        # Dispatch (workspace setup -> execute -> merge)
        dispatch_result = await self._phase_dispatch(
            topology,
            decomp_result,
            routing_result,
            context,
            phases,
        )
        phases.extend(dispatch_result.phases)

        rollup = await compute_status_rollup(
            decomposition_service=self._decomposition_service,
            task_engine=self._task_engine,
            clock=self._clock,
            context=context,
            decomp_result=decomp_result,
            phases=phases,
        )
        rollup = await relay.after_rollup(dispatch_result, rollup, phases)
        rollup = await relay.before_update_parent(rollup)

        await run_update_parent_phase(
            task_engine=self._task_engine,
            clock=self._clock,
            context=context,
            rollup=rollup,
            phases=phases,
        )

        result = CoordinationResult(
            parent_task_id=str(context.task.id),
            topology=topology,
            decomposition_result=decomp_result,
            routing_result=routing_result,
            phases=tuple(phases),
            waves=dispatch_result.waves,
            status_rollup=rollup,
            workspace_merge=dispatch_result.workspace_merge,
            total_duration_seconds=self._clock.monotonic() - pipeline_start,
            total_cost=aggregate_wave_cost(dispatch_result),
        )
        return _PipelineRun(result, routing_result, dispatch_result)

    async def _decompose_or_reuse(
        self,
        context: CoordinationContext,
        precomputed_plan: DecompositionResult | None,
        phases: list[CoordinationPhaseResult],
    ) -> DecompositionResult:
        """Produce the tree to dispatch, decomposing only when there is none.

        An approved-plan resume reuses the exact tree the human approved, so
        the built plan cannot diverge from it; the phase is still recorded,
        because a caller reading ``phases`` should see the pipeline it ran,
        not one with a step missing.

        Returns:
            The decomposition to dispatch.
        """
        if precomputed_plan is None:
            return await self._phase_decompose(context, phases)
        phases.append(
            CoordinationPhaseResult(
                phase="decompose",
                success=True,
                duration_seconds=0.0,
            )
        )
        return precomputed_plan

    async def _file_missing_children(self, result: DecompositionResult) -> int:
        """File any decomposed child the engine does not already hold.

        Decomposition mints child tasks in memory, ids derived from the plan
        items; until they are rows the engine holds, every later step acts on
        objects nothing else can see, and the assignment write before each
        wave has no row to move.

        Only the absent ones are filed. The ids are derived, so a re-dispatch
        of the same plan finds its rows already there, and saving over one
        would push a subtask that is already running back to ``CREATED``.

        Returns:
            How many children this call filed, which on a re-dispatch is
            fewer than the plan has and may be none.
        """
        engine = self._task_engine
        if engine is None:
            return 0
        # The probes are independent and there is now one per node of the
        # WHOLE tree rather than one per top-level item, so awaiting them in
        # turn costs a round trip per item on every coordinate() call.
        async with asyncio.TaskGroup() as group:
            probes = [
                (child, group.create_task(engine.get_task(str(child.id))))
                for child in result.all_tasks
            ]
        missing = [child for child, probe in probes if probe.result() is None]
        await engine.file_tasks(missing)
        return len(missing)

    async def _phase_file_children(
        self,
        result: DecompositionResult,
        phases: list[CoordinationPhaseResult],
    ) -> None:
        """File the decomposed children, recorded as a coordination phase.

        Durable task-engine I/O, so it is a phase like every other step that
        touches storage: a raw engine error escaping here would reach the
        caller without the ``partial_phases`` that say how far the pipeline
        got, which is the whole reason the phase list exists.

        Args:
            result: The decomposition whose children are filed.
            phases: The running phase list, appended to either way.

        Raises:
            CoordinationPhaseError: The children could not be filed.
        """
        phase_name = "file_children"
        start = begin_phase(phase_name, clock=self._clock)
        try:
            filed = await self._file_missing_children(result)
        except Exception as exc:
            reraise_critical(exc)
            msg = record_phase_failure(
                phase_name,
                start,
                phases,
                clock=self._clock,
                exc=exc,
                summary="Filing plan children failed",
            )
            raise CoordinationPhaseError(
                msg, phase=phase_name, partial_phases=tuple(phases)
            ) from exc
        elapsed = record_phase_success(phase_name, start, phases, clock=self._clock)
        logger.info(
            COORDINATION_PHASE_COMPLETED,
            phase=phase_name,
            # What this phase wrote, not what the plan holds: a re-dispatch
            # files nothing and a line reporting the plan's size would read
            # as having written the tree over again.
            filed_count=filed,
            subtask_count=len(result.all_tasks),
            duration_seconds=elapsed,
        )

    async def _phase_decompose(
        self,
        context: CoordinationContext,
        phases: list[CoordinationPhaseResult],
    ) -> DecompositionResult:
        """Run decomposition phase.

        Returns:
            The :class:`DecompositionResult` produced by the
            decomposition service for the parent task.

        Raises:
            CoordinationPhaseError: When decomposition fails; the
                partial phase list is attached to the error so callers
                see which phases completed.
        """
        phase_name = "decompose"
        start = begin_phase(phase_name, clock=self._clock)
        try:
            result = await self._decomposition_service.decompose_task(
                context.task, context.decomposition_context
            )
        except Exception as exc:
            reraise_critical(exc)
            msg = record_phase_failure(
                phase_name,
                start,
                phases,
                clock=self._clock,
                exc=exc,
                summary="Decomposition failed",
            )
            raise CoordinationPhaseError(
                msg, phase=phase_name, partial_phases=tuple(phases)
            ) from exc
        elapsed = record_phase_success(phase_name, start, phases, clock=self._clock)
        logger.info(
            COORDINATION_PHASE_COMPLETED,
            phase=phase_name,
            subtask_count=len(result.all_subtasks),
            levels=result.max_depth_reached + 1,
            duration_seconds=elapsed,
        )
        return result

    def _phase_route(
        self,
        context: CoordinationContext,
        decomp_result: DecompositionResult,
        phases: list[CoordinationPhaseResult],
    ) -> RoutingResult:
        """Run routing phase.

        Returns:
            The :class:`RoutingResult` mapping each routable subtask to
            an agent (with the rest in ``unroutable``).

        Raises:
            CoordinationPhaseError: When routing fails; the partial
                phase list is attached so callers see the pipeline
                shape up to the failure.
        """
        phase_name = "route"
        start = begin_phase(phase_name, clock=self._clock)
        try:
            result = self._routing_service.route(
                decomp_result,
                context.available_agents,
                context.task,
            )
        except Exception as exc:
            reraise_critical(exc)
            msg = record_phase_failure(
                phase_name,
                start,
                phases,
                clock=self._clock,
                exc=exc,
                summary="Routing failed",
            )
            raise CoordinationPhaseError(
                msg, phase=phase_name, partial_phases=tuple(phases)
            ) from exc
        elapsed = record_phase_success(phase_name, start, phases, clock=self._clock)
        logger.info(
            COORDINATION_PHASE_COMPLETED,
            phase=phase_name,
            routed=len(result.decisions),
            unroutable=len(result.unroutable),
            duration_seconds=elapsed,
        )
        return result

    def _resolve_topology(
        self,
        routing_result: RoutingResult,
    ) -> CoordinationTopology:
        """Resolve the coordination topology from routing decisions.

        Validates that all routing decisions agree on one topology.

        Returns:
            The :class:`CoordinationTopology` selected for dispatch
            (with ``AUTO`` resolved to a concrete topology).

        Raises:
            CoordinationPhaseError: If routing decisions disagree on
                topology (mixed-topology runs are not supported).
        """
        if routing_result.decisions:
            topology = routing_result.decisions[0].topology
            mixed = {d.topology for d in routing_result.decisions} - {topology}
            if mixed:
                extra = ", ".join(t.value for t in sorted(mixed, key=lambda t: t.value))
                msg = (
                    f"Inconsistent topologies in routing decisions: "
                    f"expected {topology.value!r}, also found {extra}"
                )
                logger.warning(
                    COORDINATION_PHASE_FAILED,
                    phase="resolve_topology",
                    error=msg,
                )
                raise CoordinationPhaseError(
                    msg,
                    phase="resolve_topology",
                )
        else:
            topology = (
                self._default_topology_provider()
                if self._default_topology_provider is not None
                else CoordinationTopology.SAS
            )

        # AUTO should have been resolved by TopologySelector; fallback
        if topology == CoordinationTopology.AUTO:
            logger.warning(
                COORDINATION_PHASE_FAILED,
                phase="resolve_topology",
                error=(
                    "AUTO topology was not resolved by TopologySelector; "
                    "falling back to CENTRALIZED"
                ),
            )
            topology = CoordinationTopology.CENTRALIZED

        logger.info(
            COORDINATION_TOPOLOGY_RESOLVED,
            topology=topology.value,
        )
        return topology

    async def _park_unroutable(
        self,
        routing_result: RoutingResult,
        decomp_result: DecompositionResult,
    ) -> None:
        """Park every subtask routing could not place, naming the condition.

        BLOCKED rather than FAILED because the work is still wanted and never
        ran: ``BLOCKED -> ASSIGNED`` is how it picks back up once an operator
        changes the roster, which FAILED would muddle with a run that happened.
        The reason is what makes it answerable: a bare BLOCKED row reads like
        the review gate's escalation, which waits on a different person.

        A park that fails is logged, never raised: the siblings that DID route
        are dispatched further down this same pass, and losing them to one
        bookkeeping write would turn a partial outcome into none. The cost is
        real and is named at ERROR, because a child left CREATED here has no
        second chance: ``NO_CAPABLE_AGENT`` is deliberately outside
        ``STAFFING_BLOCKED_REASONS`` so no sweep revisits it, and the rollup
        reads no CREATED row.
        """
        engine = self._task_engine
        if not routing_result.unroutable:
            return
        if engine is None:
            # Split from the no-unroutable case on purpose: "nothing needed
            # parking" and "there was nothing to park with" are opposite
            # answers, and sharing one silent return made the second read like
            # the first.
            logger.error(
                COORDINATION_UNROUTABLE_PARK_FAILED,
                parent_task_id=routing_result.parent_task_id,
                unroutable_count=len(routing_result.unroutable),
                note=(
                    "No task engine is wired, so these subtasks stay CREATED "
                    "with no assignee and no reason on the row"
                ),
            )
            return
        unroutable = set(routing_result.unroutable)
        logger.warning(
            COORDINATION_UNROUTABLE_PARKED,
            parent_task_id=routing_result.parent_task_id,
            unroutable_count=len(unroutable),
            note=(
                "No agent could take these subtasks; parked for an operator "
                "rather than left filed with no assignee"
            ),
        )
        # Plan subtask ids ARE the created task ids (DecompositionResult
        # validates it), so the role the planner asked for is recoverable here
        # and nowhere downstream: a parked row carries no role of its own, and
        # the sweep that offers to hire for it would otherwise have to reopen
        # the plan to learn what it is asking for.
        role_by_task = {
            subtask.id: subtask.required_role
            for subtask in decomp_result.all_subtasks
            if subtask.required_role is not None
        }
        for child in decomp_result.all_tasks:
            if str(child.id) not in unroutable:
                continue
            # Only a row still awaiting dispatch, decided by the coordination
            # gate's own rule and read from the engine, which is the one owner
            # of status: the decomposition's copy is what the plan WANTED and
            # a re-drive rebuilds it from the plan, so it says CREATED for a
            # row that has been parked for hours. Routing re-runs over every
            # subtask on every pass, so without this a re-driven plan re-parks
            # rows that are ALREADY parked: the engine refuses
            # BLOCKED -> BLOCKED and the refusal surfaced as a raw ValueError
            # at WARNING every cadence, for ever, against a row that was in
            # exactly the state this park wanted.
            if not awaits_dispatch(await self._live_status(engine, str(child.id))):
                continue
            required_role = role_by_task.get(str(child.id))
            metadata = dict(child.metadata)
            if required_role is not None:
                metadata[UNROUTABLE_ROLE_KEY] = str(required_role)
            try:
                result = await engine.submit(
                    TransitionTaskMutation(
                        request_id=uuid4().hex,
                        requested_by=_UNROUTABLE_ACTOR,
                        task_id=str(child.id),
                        target_status=TaskStatus.BLOCKED,
                        reason=_UNROUTABLE_REASON,
                        overrides={
                            "blocked_reason": BlockedReason.NO_CAPABLE_AGENT,
                            "metadata": metadata,
                        },
                    )
                )
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                # lint-allow: swallow-ok -- one contended row must not cost the
                # siblings this pass is about to dispatch their whole run.
                reraise_critical(exc)
                logger.error(
                    COORDINATION_UNROUTABLE_PARK_FAILED,
                    subtask_id=str(child.id),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    note=(
                        "This subtask stays CREATED with no assignee and no "
                        "reason; nothing sweeps it and no rollup reads it"
                    ),
                )
            else:
                # A refused mutation is a RESULT here, not a raise, so the
                # handler above never sees it: without this the one failure
                # mode the engine actually produces was the one nobody was
                # told about, and the promise that a failed park is named
                # held only for the failures that could not happen.
                if not result.success:
                    logger.error(
                        COORDINATION_UNROUTABLE_PARK_FAILED,
                        subtask_id=str(child.id),
                        error_type="TaskMutationRejected",
                        error=result.error or "park rejected with no error detail",
                        note=(
                            "This subtask stays where it was with no reason on "
                            "the row; nothing sweeps it and no rollup reads it"
                        ),
                    )

    @staticmethod
    async def _live_status(engine: TaskEngine, task_id: str) -> TaskStatus | None:
        """Read one subtask's status from the engine that owns it.

        Returns:
            The status the engine holds, or ``None`` when it holds no row and
            when the read itself fails. Both mean the same thing here: this
            pass has no evidence the row is already settled, so the park is
            attempted and its own verdict decides.
        """
        try:
            live = await engine.get_task(task_id)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- an unreadable row must not cost the
            # siblings this pass is about to dispatch their whole run.
            reraise_critical(exc)
            logger.warning(
                COORDINATION_UNROUTABLE_PARK_FAILED,
                subtask_id=task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="could not read the row before parking it; parking anyway",
            )
            return None
        return None if live is None else live.status

    def _validate_routing(
        self,
        routing_result: RoutingResult,
        phases: list[CoordinationPhaseResult],
    ) -> None:
        """Validate routing result; fail if all subtasks unroutable.

        Raises:
            CoordinationPhaseError: When every subtask landed in
                ``routing_result.unroutable`` and none were dispatched
                to an agent.
        """
        if not routing_result.decisions and routing_result.unroutable:
            error_msg = (
                f"All {len(routing_result.unroutable)} subtask(s) are unroutable"
            )
            logger.warning(
                COORDINATION_PHASE_FAILED,
                phase="validate",
                unroutable_count=len(routing_result.unroutable),
                error=error_msg,
            )
            phase = CoordinationPhaseResult(
                phase="validate",
                success=False,
                duration_seconds=0.0,
                error=error_msg,
            )
            phases.append(phase)
            msg = "All subtasks are unroutable -- no agents matched"
            raise CoordinationPhaseError(
                msg,
                phase="validate",
                partial_phases=tuple(phases),
            )

    async def _resolve_repo_root(self, project_id: NotBlankStr | None) -> Path | None:
        """Resolve the project's on-disk repo root for push-queue merges.

        Returns:
            The :class:`Path` to the per-project workspace root, or
            ``None`` when there is no project context or no project-
            workspace service is wired (the empty-company / no-durable-
            backing path), which makes the dispatch merge fall back to
            the in-memory ``merge_group``.
        """
        if project_id is None or self._project_workspace_service is None:
            return None
        workspace = await self._project_workspace_service.get_or_provision(project_id)
        return Path(workspace.workspace_path)

    async def _phase_dispatch(
        self,
        topology: CoordinationTopology,
        decomp_result: DecompositionResult,
        routing_result: RoutingResult,
        context: CoordinationContext,
        phases: list[CoordinationPhaseResult],
    ) -> DispatchResult:
        """Run dispatch phase with error wrapping.

        Returns:
            The :class:`DispatchResult` from the topology-selected
            dispatcher, including its per-wave phases.

        Raises:
            CoordinationPhaseError: When dispatch fails (any
                non-``CoordinationPhaseError`` exception is wrapped
                into one with the partial phase list).
        """
        phase_name = "dispatch"
        start = begin_phase(phase_name, clock=self._clock)
        try:
            dispatcher = select_dispatcher(
                topology,
                clock=self._clock,
                assignment_writer=AssignmentWriter(self._task_engine),
            )
            project_id = context.task.project
            repo_root = await self._resolve_repo_root(project_id)
            return await dispatcher.dispatch(
                decomposition_result=decomp_result,
                routing_result=routing_result,
                parallel_executor=self._parallel_executor,
                workspace_service=self._workspace_service,
                config=context.config,
                project_id=project_id,
                repo_root=repo_root,
            )
        except CoordinationPhaseError:
            raise
        except Exception as exc:
            reraise_critical(exc)
            msg = record_phase_failure(
                phase_name,
                start,
                phases,
                clock=self._clock,
                exc=exc,
                summary="Dispatch failed",
            )
            raise CoordinationPhaseError(
                msg, phase=phase_name, partial_phases=tuple(phases)
            ) from exc
