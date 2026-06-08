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
from typing import TYPE_CHECKING, Final

from synthorg.budget.coordination_collector import CollectionInputs
from synthorg.budget.currency import assert_currencies_match
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task_enums import CoordinationTopology
from synthorg.core.types import NotBlankStr
from synthorg.engine.coordination.attribution import (
    AgentContribution,
    CoordinationResultWithAttribution,
    build_agent_contributions,
)
from synthorg.engine.coordination.dispatcher_factory import select_dispatcher
from synthorg.engine.coordination.dispatcher_types import DispatchResult
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
from synthorg.engine.errors import CoordinationPhaseError
from synthorg.engine.routing.models import RoutingResult
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.coordination import (
    COORDINATION_CLEANUP_FAILED,
    COORDINATION_COMPLETED,
    COORDINATION_FAILED,
    COORDINATION_PHASE_COMPLETED,
    COORDINATION_PHASE_FAILED,
    COORDINATION_PHASE_STARTED,
    COORDINATION_STARTED,
    COORDINATION_TOPOLOGY_RESOLVED,
)

if TYPE_CHECKING:
    # Concrete services faked in tests; a runtime import would make typeguard
    # enforce a nominal isinstance the fakes cannot satisfy.
    from synthorg.budget.coordination_collector import (
        CoordinationMetricsCollector,
    )
    from synthorg.engine.decomposition.service import DecompositionService
    from synthorg.engine.middleware.coordination_protocol import (
        CoordinationMiddlewareChain,
    )
    from synthorg.engine.parallel import ParallelExecutor
    from synthorg.engine.routing.service import TaskRoutingService
    from synthorg.engine.task_engine import TaskEngine
    from synthorg.engine.workspace.project_workspace_service import (
        ProjectWorkspaceService,
    )
    from synthorg.engine.workspace.service import WorkspaceIsolationService
    from synthorg.hr.performance.tracker import PerformanceTracker

logger = get_logger(__name__)

# Logging actor for the multi-agent (system-level) metrics collection.
# The recorded ``CoordinationMetricsRecord`` carries ``agent_id=None``
# (no single lead in a coordinated run); this label only tags the
# collector's observability events / overhead alerts.
_COORDINATOR_ACTOR: str = "coordinator"

# Upper bound on the post-completion metrics-collection hook. The
# collector awaits the message bus and similarity computer; a degraded
# dependency must not wedge an already-completed coordination run.
# Like ``budget/enforcer._DEFAULT_TIMEOUT_SEC`` this bounds a
# never-fatal drain, not operator-tunable policy, so it stays a typed
# constant rather than a registered setting.
_METRICS_COLLECT_TIMEOUT_SECONDS: Final[float] = 30.0


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
        parallel_executor: ParallelExecutor,
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

    async def coordinate(
        self,
        context: CoordinationContext,
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

        Returns:
            CoordinationResultWithAttribution wrapping the result
            with per-agent contribution data.

        Raises:
            CoordinationPhaseError: When a critical phase fails.
        """
        pipeline_start = self._clock.monotonic()
        task = context.task
        phases: list[CoordinationPhaseResult] = []

        logger.info(
            COORDINATION_STARTED,
            parent_task_id=str(task.id),
            agent_count=len(context.available_agents),
        )

        # Build coordination middleware context if chain is wired.
        mw_chain = self._coordination_chain

        try:
            # Middleware hook: before_decompose
            if mw_chain is not None:
                from synthorg.engine.middleware.coordination_protocol import (  # noqa: PLC0415
                    CoordinationMiddlewareContext,
                )

                mw_ctx = CoordinationMiddlewareContext(
                    coordination_context=context,
                )
                mw_ctx = await mw_chain.run_before_decompose(mw_ctx)

            # Decompose
            decomp_result = await self._phase_decompose(context, phases)

            # Middleware hook: after_decompose
            if mw_chain is not None:
                mw_ctx = mw_ctx.model_copy(
                    update={
                        "decomposition_result": decomp_result,
                        "phases": tuple(phases),
                    },
                )
                mw_ctx = await mw_chain.run_after_decompose(mw_ctx)
                # Propagate middleware-mutated artifacts
                if mw_ctx.decomposition_result is not None:
                    decomp_result = mw_ctx.decomposition_result

            # Route
            routing_result = self._phase_route(context, decomp_result, phases)

            # Middleware: before_dispatch.  Runs BEFORE validation +
            # topology resolution so that any routing mutations the
            # middleware applies (e.g. re-routing unassigned subtasks,
            # enriching topology metadata) are included in the inputs
            # those two phases consume.  Previously the order was
            # validate -> resolve -> middleware, which meant middleware
            # edits to ``routing_result`` never influenced topology.
            if mw_chain is not None:
                mw_ctx = mw_ctx.model_copy(
                    update={
                        "routing_result": routing_result,
                        "phases": tuple(phases),
                    },
                )
                mw_ctx = await mw_chain.run_before_dispatch(mw_ctx)
                if mw_ctx.routing_result is not None:
                    routing_result = mw_ctx.routing_result

            # Validate -- fail fast if all subtasks are unroutable.
            # Runs BEFORE resolving topology so the deterministic
            # routing error surfaces without first calling
            # ``default_topology_provider()`` (which may read runtime
            # settings or raise).
            self._validate_routing(routing_result, phases)

            # Resolve topology (only reached for dispatchable work).
            # Wrapped in try/except because
            # ``default_topology_provider()`` may read runtime settings
            # or raise -- any failure must surface as a failed
            # coordination phase with a proper ``CoordinationPhaseError``
            # + partial_phases so the caller sees the partial pipeline
            # instead of an opaque traceback.
            topology_phase = "resolve_topology"
            topology_start = self._clock.monotonic()
            try:
                topology = self._resolve_topology(routing_result)
            except CoordinationPhaseError as phase_exc:
                # ``_resolve_topology`` raises ``CoordinationPhaseError``
                # for mixed-topology routing but does NOT append a phase
                # marker itself -- record the failure here so the phase
                # list surfaces the topology-resolution step, mirroring
                # the decomposition/routing/dispatch handlers below.
                # Re-raise a NEW ``CoordinationPhaseError`` carrying
                # the updated ``partial_phases`` so callers can see
                # which phases completed before the failure (the
                # original exception was raised before this phase
                # marker existed in ``phases``).
                elapsed = self._clock.monotonic() - topology_start
                # Always log at WARNING before re-raising. This covers
                # both (a) mixed-topology errors ``_resolve_topology``
                # logs internally AND (b) provider-originated failures
                # raised by ``default_topology_provider()`` or any
                # future topology-resolution subsystem that did not
                # log before raising. One entry per failure path is
                # the coding-guideline contract.
                logger.warning(
                    COORDINATION_PHASE_FAILED,
                    phase=topology_phase,
                    error_type=type(phase_exc).__name__,
                    error=safe_error_description(phase_exc),
                    empty_routing_decisions=not routing_result.decisions,
                )
                phases.append(
                    CoordinationPhaseResult(
                        phase=topology_phase,
                        success=False,
                        duration_seconds=elapsed,
                        error=safe_error_description(phase_exc),
                    )
                )
                raise CoordinationPhaseError(
                    str(phase_exc),
                    phase=topology_phase,
                    partial_phases=tuple(phases),
                ) from phase_exc
            except Exception as exc:
                reraise_critical(exc)
                elapsed = self._clock.monotonic() - topology_start
                logger.warning(
                    COORDINATION_PHASE_FAILED,
                    phase=topology_phase,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                phases.append(
                    CoordinationPhaseResult(
                        phase=topology_phase,
                        success=False,
                        duration_seconds=elapsed,
                        error=safe_error_description(exc),
                    )
                )
                msg = f"Topology resolution failed: {safe_error_description(exc)}"
                raise CoordinationPhaseError(
                    msg,
                    phase=topology_phase,
                    partial_phases=tuple(phases),
                ) from exc

            # Dispatch (workspace setup -> execute -> merge)
            dispatch_result = await self._phase_dispatch(
                topology,
                decomp_result,
                routing_result,
                context,
                phases,
            )
            phases.extend(dispatch_result.phases)

            # Rollup
            rollup = compute_status_rollup(
                decomposition_service=self._decomposition_service,
                clock=self._clock,
                context=context,
                dispatch_result=dispatch_result,
                decomp_result=decomp_result,
                phases=phases,
            )

            # Middleware hook: after_rollup
            if mw_chain is not None:
                mw_ctx = mw_ctx.model_copy(
                    update={
                        "dispatch_result": dispatch_result,
                        "status_rollup": rollup,
                        "phases": tuple(phases),
                    },
                )
                mw_ctx = await mw_chain.run_after_rollup(mw_ctx)
                # Propagate middleware-mutated rollup
                rollup = mw_ctx.status_rollup

            # Middleware hook: before_update_parent
            if mw_chain is not None:
                mw_ctx = await mw_chain.run_before_update_parent(
                    mw_ctx,
                )
                # Propagate middleware-sanitized rollup
                rollup = mw_ctx.status_rollup

            # Update parent task
            await run_update_parent_phase(
                task_engine=self._task_engine,
                clock=self._clock,
                context=context,
                rollup=rollup,
                phases=phases,
            )

            total_duration = self._clock.monotonic() - pipeline_start
            wave_results = tuple(
                w.execution_result
                for w in dispatch_result.waves
                if w.execution_result is not None
            )
            # Waves with no completed results report ``currency=None``
            # AND ``total_cost=0`` (see ``ParallelExecutionResult``);
            # filtering them out before the guard is correct because
            # they cannot contribute to the cross-wave aggregate, and
            # passing ``None`` to ``assert_currencies_match`` would
            # otherwise fail closed under the missing-currency rule.
            assert_currencies_match(
                er.currency for er in wave_results if er.currency is not None
            )
            total_cost = sum(er.total_cost for er in wave_results)

            result = CoordinationResult(
                parent_task_id=str(task.id),
                topology=topology,
                decomposition_result=decomp_result,
                routing_result=routing_result,
                phases=tuple(phases),
                waves=dispatch_result.waves,
                status_rollup=rollup,
                workspace_merge=dispatch_result.workspace_merge,
                total_duration_seconds=total_duration,
                total_cost=total_cost,
            )

        except CoordinationPhaseError:
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
            topology=topology.value,
            is_success=result.is_success,
            total_duration_seconds=total_duration,
            total_cost=total_cost,
        )

        # Post-pipeline: build per-agent attribution.
        # Guard so attribution/tracker failures don't fail a completed run.
        contributions: tuple[AgentContribution, ...] = ()
        try:
            contributions = build_agent_contributions(
                routing_result,
                dispatch_result.waves,
            )
        except Exception as attr_exc:
            reraise_critical(attr_exc)
            logger.warning(
                COORDINATION_CLEANUP_FAILED,
                parent_task_id=str(task.id),
                error_type=type(attr_exc).__name__,
                error=safe_error_description(attr_exc),
                context="post_completion_attribution_build",
            )

        if self._performance_tracker is not None and contributions:
            try:
                await self._performance_tracker.record_coordination_contributions(
                    contributions,
                )
            except Exception as tracker_exc:
                reraise_critical(tracker_exc)
                logger.warning(
                    COORDINATION_CLEANUP_FAILED,
                    parent_task_id=str(task.id),
                    error_type=type(tracker_exc).__name__,
                    error=safe_error_description(tracker_exc),
                    context="post_completion_tracker_write",
                )

        await self._collect_coordination_metrics(
            task_id=str(task.id),
            dispatch_result=dispatch_result,
        )

        return CoordinationResultWithAttribution(
            result=result,
            agent_contributions=contributions,
        )

    async def _collect_coordination_metrics(
        self,
        *,
        task_id: str,
        dispatch_result: DispatchResult,
    ) -> None:
        """Compute and record the multi-agent coordination metrics.

        Never fatal: a collector failure must not fail an already
        completed coordination run (mirrors the ``_performance_tracker``
        guard above). Skipped when no collector is wired or no sub-agent
        produced a result. ``asyncio.wait_for`` bounds the hook so a
        degraded message bus or similarity computer cannot wedge a
        completed run; a timeout surfaces as ``TimeoutError`` in the
        guard below (logged via ``error_type``).
        """
        collector = self._coordination_metrics_collector
        if collector is None:
            return
        inputs = self._build_collection_inputs(task_id, dispatch_result)
        if inputs is None:
            return
        try:
            await asyncio.wait_for(
                collector.collect(inputs),
                timeout=_METRICS_COLLECT_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                COORDINATION_CLEANUP_FAILED,
                parent_task_id=task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                context="post_completion_coordination_metrics",
            )

    def _build_collection_inputs(
        self,
        task_id: str,
        dispatch_result: DispatchResult,
    ) -> CollectionInputs | None:
        """Aggregate sub-agent results into the collector inputs.

        The aggregate ``ExecutionResult`` carries the team-wide turn
        records (``model_copy`` off a real sub-agent result, swapping
        only ``turns`` since the collector reads nothing else off it)
        so ``turns_mas`` is the total reasoning turns across the
        system. Multi-agent coordination has no single lead, so
        ``agent_id`` is the system-level ``_COORDINATOR_ACTOR`` label.

        Returns:
            The :class:`CollectionInputs` payload ready for the
            collector, or ``None`` when no sub-agent produced a result.
        """
        outcomes = [
            outcome
            for wave in dispatch_result.waves
            if wave.execution_result is not None
            for outcome in wave.execution_result.outcomes
        ]
        results = [outcome.result for outcome in outcomes if outcome.result is not None]
        if not results:
            return None
        # Count every dispatched participant, including ones whose
        # subtask failed (no result), so team-level metrics are not
        # skewed low by partial failures.
        participating_agents = {outcome.agent_id for outcome in outcomes}
        aggregate_turns = tuple(
            turn for r in results for turn in r.execution_result.turns
        )
        aggregate = results[0].execution_result.model_copy(
            update={"turns": aggregate_turns},
        )
        # Sum durations per agent so StragglerGap reflects each actor's
        # total time across waves rather than a single subtask slice.
        durations_by_agent: dict[str, float] = {}
        for r in results:
            durations_by_agent[r.agent_id] = (
                durations_by_agent.get(r.agent_id, 0.0) + r.duration_seconds
            )
        return CollectionInputs(
            execution_result=aggregate,
            agent_id=_COORDINATOR_ACTOR,
            task_id=task_id,
            team_size=len(participating_agents),
            agent_durations=tuple(durations_by_agent.items()),
            agent_outputs=tuple(
                r.completion_summary for r in results if r.completion_summary
            ),
            is_multi_agent=True,
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
        start = self._clock.monotonic()
        phase_name = "decompose"

        logger.info(COORDINATION_PHASE_STARTED, phase=phase_name)
        try:
            result = await self._decomposition_service.decompose_task(
                context.task, context.decomposition_context
            )
        except Exception as exc:
            reraise_critical(exc)
            elapsed = self._clock.monotonic() - start
            logger.warning(
                COORDINATION_PHASE_FAILED,
                phase=phase_name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            phase = CoordinationPhaseResult(
                phase=phase_name,
                success=False,
                duration_seconds=elapsed,
                error=safe_error_description(exc),
            )
            phases.append(phase)
            msg = f"Decomposition failed: {safe_error_description(exc)}"
            raise CoordinationPhaseError(
                msg,
                phase=phase_name,
                partial_phases=tuple(phases),
            ) from exc

        elapsed = self._clock.monotonic() - start
        phases.append(
            CoordinationPhaseResult(
                phase=phase_name,
                success=True,
                duration_seconds=elapsed,
            )
        )
        logger.info(
            COORDINATION_PHASE_COMPLETED,
            phase=phase_name,
            subtask_count=len(result.plan.subtasks),
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
        start = self._clock.monotonic()
        phase_name = "route"

        logger.info(COORDINATION_PHASE_STARTED, phase=phase_name)
        try:
            result = self._routing_service.route(
                decomp_result,
                context.available_agents,
                context.task,
            )
        except Exception as exc:
            reraise_critical(exc)
            elapsed = self._clock.monotonic() - start
            logger.warning(
                COORDINATION_PHASE_FAILED,
                phase=phase_name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            phase = CoordinationPhaseResult(
                phase=phase_name,
                success=False,
                duration_seconds=elapsed,
                error=safe_error_description(exc),
            )
            phases.append(phase)
            msg = f"Routing failed: {safe_error_description(exc)}"
            raise CoordinationPhaseError(
                msg,
                phase=phase_name,
                partial_phases=tuple(phases),
            ) from exc

        elapsed = self._clock.monotonic() - start
        phases.append(
            CoordinationPhaseResult(
                phase=phase_name,
                success=True,
                duration_seconds=elapsed,
            )
        )
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
        start = self._clock.monotonic()
        phase_name = "dispatch"

        logger.info(COORDINATION_PHASE_STARTED, phase=phase_name)
        try:
            dispatcher = select_dispatcher(topology, clock=self._clock)
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
            elapsed = self._clock.monotonic() - start
            logger.warning(
                COORDINATION_PHASE_FAILED,
                phase=phase_name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            phase = CoordinationPhaseResult(
                phase=phase_name,
                success=False,
                duration_seconds=elapsed,
                error=safe_error_description(exc),
            )
            phases.append(phase)
            msg = f"Dispatch failed: {safe_error_description(exc)}"
            raise CoordinationPhaseError(
                msg,
                phase=phase_name,
                partial_phases=tuple(phases),
            ) from exc
