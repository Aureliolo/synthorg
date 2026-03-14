"""Multi-agent coordination service.

Orchestrates the end-to-end pipeline: decompose → route → resolve
topology → dispatch (workspace setup → execute waves → merge) →
rollup → update parent task.
"""

import time
from typing import TYPE_CHECKING
from uuid import uuid4

from ai_company.core.enums import CoordinationTopology, TaskStatus
from ai_company.engine.coordination.dispatchers import (
    DispatchResult,
    select_dispatcher,
)
from ai_company.engine.coordination.models import (
    CoordinationPhaseResult,
    CoordinationResult,
)
from ai_company.engine.errors import CoordinationPhaseError
from ai_company.engine.task_engine_models import TransitionTaskMutation
from ai_company.observability import get_logger
from ai_company.observability.events.coordination import (
    COORDINATION_COMPLETED,
    COORDINATION_FAILED,
    COORDINATION_PHASE_COMPLETED,
    COORDINATION_PHASE_FAILED,
    COORDINATION_PHASE_STARTED,
    COORDINATION_STARTED,
    COORDINATION_TOPOLOGY_RESOLVED,
)

if TYPE_CHECKING:
    from ai_company.engine.coordination.models import CoordinationContext
    from ai_company.engine.decomposition.models import (
        DecompositionResult,
        SubtaskStatusRollup,
    )
    from ai_company.engine.decomposition.service import DecompositionService
    from ai_company.engine.parallel import ParallelExecutor
    from ai_company.engine.routing.models import RoutingResult
    from ai_company.engine.routing.service import TaskRoutingService
    from ai_company.engine.task_engine import TaskEngine
    from ai_company.engine.workspace.service import WorkspaceIsolationService

logger = get_logger(__name__)


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
        task_engine: Optional task engine for parent status updates.
    """

    __slots__ = (
        "_decomposition_service",
        "_parallel_executor",
        "_routing_service",
        "_task_engine",
        "_workspace_service",
    )

    def __init__(
        self,
        *,
        decomposition_service: DecompositionService,
        routing_service: TaskRoutingService,
        parallel_executor: ParallelExecutor,
        workspace_service: WorkspaceIsolationService | None = None,
        task_engine: TaskEngine | None = None,
    ) -> None:
        self._decomposition_service = decomposition_service
        self._routing_service = routing_service
        self._parallel_executor = parallel_executor
        self._workspace_service = workspace_service
        self._task_engine = task_engine

    async def coordinate(
        self,
        context: CoordinationContext,
    ) -> CoordinationResult:
        """Run the full multi-agent coordination pipeline.

        Pipeline:
            1. Decompose task into subtasks via DecompositionService.
            2. Route subtasks to agents via TaskRoutingService.
            3. Resolve topology from routing decisions.
            4. Validate: fail if ALL subtasks are unroutable.
            5. Select dispatcher and execute waves.
            6. Rollup subtask statuses.
            7. Update parent task via TaskEngine (if provided).

        Args:
            context: Coordination context with task, agents, and config.

        Returns:
            CoordinationResult with all phase outcomes.

        Raises:
            CoordinationPhaseError: When a critical phase fails.
        """
        pipeline_start = time.monotonic()
        task = context.task
        phases: list[CoordinationPhaseResult] = []

        logger.info(
            COORDINATION_STARTED,
            parent_task_id=task.id,
            agent_count=len(context.available_agents),
        )

        try:
            # Phase 1: Decompose
            decomp_result = await self._phase_decompose(context, phases)

            # Phase 2: Route
            routing_result = self._phase_route(context, decomp_result, phases)

            # Phase 3: Resolve topology
            topology = self._resolve_topology(routing_result)

            # Phase 4: Validate — fail if all unroutable
            self._validate_routing(routing_result, phases)

            # Phase 5: Dispatch (workspace setup → execute → merge)
            dispatch_result = await self._phase_dispatch(
                topology,
                decomp_result,
                routing_result,
                context,
                phases,
            )
            phases.extend(dispatch_result.phases)

            # Phase 6: Rollup
            rollup = self._phase_rollup(context, dispatch_result, decomp_result, phases)

            # Phase 7: Update parent task
            await self._phase_update_parent(context, rollup, phases)

            total_duration = time.monotonic() - pipeline_start
            total_cost = sum(
                w.execution_result.total_cost_usd
                for w in dispatch_result.waves
                if w.execution_result is not None
            )

            result = CoordinationResult(
                parent_task_id=task.id,
                topology=topology,
                decomposition_result=decomp_result,
                routing_result=routing_result,
                phases=tuple(phases),
                waves=dispatch_result.waves,
                status_rollup=rollup,
                workspace_merge=dispatch_result.workspace_merge,
                total_duration_seconds=total_duration,
                total_cost_usd=total_cost,
            )

        except CoordinationPhaseError:
            raise
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.exception(
                COORDINATION_FAILED,
                parent_task_id=task.id,
                error=str(exc),
            )
            raise

        logger.info(
            COORDINATION_COMPLETED,
            parent_task_id=task.id,
            topology=topology.value,
            is_success=result.is_success,
            total_duration_seconds=total_duration,
            total_cost_usd=total_cost,
        )

        return result

    async def _phase_decompose(
        self,
        context: CoordinationContext,
        phases: list[CoordinationPhaseResult],
    ) -> DecompositionResult:
        """Run decomposition phase."""
        start = time.monotonic()
        phase_name = "decompose"

        logger.info(COORDINATION_PHASE_STARTED, phase=phase_name)
        try:
            result = await self._decomposition_service.decompose_task(
                context.task, context.decomposition_context
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            elapsed = time.monotonic() - start
            logger.warning(
                COORDINATION_PHASE_FAILED,
                phase=phase_name,
                error=str(exc),
            )
            phase = CoordinationPhaseResult(
                phase=phase_name,
                success=False,
                duration_seconds=elapsed,
                error=str(exc),
            )
            phases.append(phase)
            msg = f"Decomposition failed: {exc}"
            raise CoordinationPhaseError(
                msg,
                phase=phase_name,
                partial_phases=tuple(phases),
            ) from exc

        elapsed = time.monotonic() - start
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
        """Run routing phase."""
        start = time.monotonic()
        phase_name = "route"

        logger.info(COORDINATION_PHASE_STARTED, phase=phase_name)
        try:
            result = self._routing_service.route(
                decomp_result,
                context.available_agents,
                context.task,
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            elapsed = time.monotonic() - start
            logger.warning(
                COORDINATION_PHASE_FAILED,
                phase=phase_name,
                error=str(exc),
            )
            phase = CoordinationPhaseResult(
                phase=phase_name,
                success=False,
                duration_seconds=elapsed,
                error=str(exc),
            )
            phases.append(phase)
            msg = f"Routing failed: {exc}"
            raise CoordinationPhaseError(
                msg,
                phase=phase_name,
                partial_phases=tuple(phases),
            ) from exc

        elapsed = time.monotonic() - start
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
            topology = CoordinationTopology.SAS

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
        """Validate routing result — fail if all subtasks unroutable."""
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
            msg = "All subtasks are unroutable — no agents matched"
            raise CoordinationPhaseError(
                msg,
                phase="validate",
                partial_phases=tuple(phases),
            )

    async def _phase_dispatch(
        self,
        topology: CoordinationTopology,
        decomp_result: DecompositionResult,
        routing_result: RoutingResult,
        context: CoordinationContext,
        phases: list[CoordinationPhaseResult],
    ) -> DispatchResult:
        """Run dispatch phase with error wrapping."""
        start = time.monotonic()
        phase_name = "dispatch"

        logger.info(COORDINATION_PHASE_STARTED, phase=phase_name)
        try:
            dispatcher = select_dispatcher(topology)
            return await dispatcher.dispatch(
                decomposition_result=decomp_result,
                routing_result=routing_result,
                parallel_executor=self._parallel_executor,
                workspace_service=self._workspace_service,
                config=context.config,
            )
        except CoordinationPhaseError:
            raise
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            elapsed = time.monotonic() - start
            logger.warning(
                COORDINATION_PHASE_FAILED,
                phase=phase_name,
                error=str(exc),
            )
            phase = CoordinationPhaseResult(
                phase=phase_name,
                success=False,
                duration_seconds=elapsed,
                error=str(exc),
            )
            phases.append(phase)
            msg = f"Dispatch failed: {exc}"
            raise CoordinationPhaseError(
                msg,
                phase=phase_name,
                partial_phases=tuple(phases),
            ) from exc

    def _phase_rollup(
        self,
        context: CoordinationContext,
        dispatch_result: DispatchResult,
        decomp_result: DecompositionResult,
        phases: list[CoordinationPhaseResult],
    ) -> SubtaskStatusRollup | None:
        """Compute status rollup from execution outcomes.

        Includes all expected subtasks — those missing from waves
        (unroutable, blocked by prerequisites, or skipped by
        fail-fast) are counted as BLOCKED.
        """
        start = time.monotonic()
        phase_name = "rollup"

        logger.info(COORDINATION_PHASE_STARTED, phase=phase_name)
        try:
            # Collect statuses from wave outcomes
            statuses: list[TaskStatus] = []
            for wave in dispatch_result.waves:
                if wave.execution_result is None:
                    statuses.extend(TaskStatus.BLOCKED for _ in wave.subtask_ids)
                    continue

                for outcome in wave.execution_result.outcomes:
                    if outcome.is_success:
                        statuses.append(TaskStatus.COMPLETED)
                    else:
                        statuses.append(TaskStatus.FAILED)

            # Fill missing subtasks as BLOCKED (unroutable,
            # blocked prerequisites, or fail-fast skipped)
            expected_count = len(decomp_result.plan.subtasks)
            missing_count = expected_count - len(statuses)
            if missing_count > 0:
                statuses.extend(TaskStatus.BLOCKED for _ in range(missing_count))

            rollup = self._decomposition_service.rollup_status(
                context.task.id,
                tuple(statuses),
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            elapsed = time.monotonic() - start
            logger.warning(
                COORDINATION_PHASE_FAILED,
                phase=phase_name,
                error=str(exc),
            )
            phases.append(
                CoordinationPhaseResult(
                    phase=phase_name,
                    success=False,
                    duration_seconds=elapsed,
                    error=str(exc),
                )
            )
            return None

        elapsed = time.monotonic() - start
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
            duration_seconds=elapsed,
        )
        return rollup

    async def _phase_update_parent(
        self,
        context: CoordinationContext,
        rollup: SubtaskStatusRollup | None,
        phases: list[CoordinationPhaseResult],
    ) -> None:
        """Update parent task status via TaskEngine if available."""
        if self._task_engine is None:
            return
        if rollup is None:
            phase_name = "update_parent"
            note = "Skipped — rollup is None (rollup phase failed)"
            logger.warning(
                COORDINATION_PHASE_FAILED,
                phase=phase_name,
                note=note,
            )
            phases.append(
                CoordinationPhaseResult(
                    phase=phase_name,
                    success=False,
                    duration_seconds=0.0,
                    error=note,
                )
            )
            return

        start = time.monotonic()
        phase_name = "update_parent"

        logger.info(COORDINATION_PHASE_STARTED, phase=phase_name)
        try:
            mutation = TransitionTaskMutation(
                request_id=str(uuid4()),
                requested_by="coordinator",
                task_id=context.task.id,
                target_status=rollup.derived_parent_status,
                reason=(
                    f"Coordination rollup: "
                    f"{rollup.completed}/{rollup.total} completed, "
                    f"{rollup.failed}/{rollup.total} failed"
                ),
            )
            result = await self._task_engine.submit(mutation)
            elapsed = time.monotonic() - start

            if result.success:
                logger.info(
                    COORDINATION_PHASE_COMPLETED,
                    phase=phase_name,
                    duration_seconds=elapsed,
                )
            else:
                logger.warning(
                    COORDINATION_PHASE_FAILED,
                    phase=phase_name,
                    error=result.error,
                )
            phases.append(
                CoordinationPhaseResult(
                    phase=phase_name,
                    success=result.success,
                    duration_seconds=elapsed,
                    error=result.error,
                )
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            elapsed = time.monotonic() - start
            logger.warning(
                COORDINATION_PHASE_FAILED,
                phase=phase_name,
                error=str(exc),
            )
            phases.append(
                CoordinationPhaseResult(
                    phase=phase_name,
                    success=False,
                    duration_seconds=elapsed,
                    error=str(exc),
                )
            )
