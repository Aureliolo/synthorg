"""Topology-driven dispatch strategies.

Each dispatcher implementation maps a ``CoordinationTopology`` to a
specific execution pattern: wave construction, workspace lifecycle,
and merge orchestration.
"""

import time
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.enums import CoordinationTopology
from synthorg.engine.coordination.group_builder import build_execution_waves
from synthorg.engine.coordination.models import (
    CoordinationPhaseResult,
    CoordinationWave,
)
from synthorg.engine.errors import CoordinationError
from synthorg.engine.workspace.models import (
    Workspace,
    WorkspaceGroupResult,
    WorkspaceRequest,
)
from synthorg.observability import get_logger
from synthorg.observability.events.coordination import (
    COORDINATION_CLEANUP_COMPLETED,
    COORDINATION_CLEANUP_FAILED,
    COORDINATION_CLEANUP_STARTED,
    COORDINATION_PHASE_COMPLETED,
    COORDINATION_PHASE_FAILED,
    COORDINATION_PHASE_STARTED,
    COORDINATION_TOPOLOGY_RESOLVED,
    COORDINATION_WAVE_COMPLETED,
    COORDINATION_WAVE_STARTED,
)

if TYPE_CHECKING:
    from synthorg.engine.coordination.config import CoordinationConfig
    from synthorg.engine.decomposition.models import DecompositionResult
    from synthorg.engine.parallel import ParallelExecutor
    from synthorg.engine.parallel_models import ParallelExecutionGroup
    from synthorg.engine.routing.models import RoutingResult
    from synthorg.engine.workspace.service import WorkspaceIsolationService

logger = get_logger(__name__)


class DispatchResult(BaseModel):
    """Result of a topology dispatcher's execution.

    Attributes:
        waves: Executed waves with their results.
        workspaces: Workspaces created during execution.
        workspace_merge: Merge result if workspaces were merged.
        phases: Phase results generated during dispatch.
    """

    model_config = ConfigDict(frozen=True)

    waves: tuple[CoordinationWave, ...] = Field(
        default=(),
        description="Executed waves",
    )
    workspaces: tuple[Workspace, ...] = Field(
        default=(),
        description="Workspaces created during execution",
    )
    workspace_merge: WorkspaceGroupResult | None = Field(
        default=None,
        description="Workspace merge result",
    )
    phases: tuple[CoordinationPhaseResult, ...] = Field(
        default=(),
        description="Phase results from dispatch",
    )


@runtime_checkable
class TopologyDispatcher(Protocol):
    """Protocol for topology-specific dispatch strategies."""

    async def dispatch(
        self,
        *,
        decomposition_result: DecompositionResult,
        routing_result: RoutingResult,
        parallel_executor: ParallelExecutor,
        workspace_service: WorkspaceIsolationService | None,
        config: CoordinationConfig,
    ) -> DispatchResult:
        """Execute subtasks according to topology-specific rules.

        Args:
            decomposition_result: Decomposition with subtasks.
            routing_result: Routing decisions for subtasks.
            parallel_executor: Executor for parallel agent runs.
            workspace_service: Optional workspace isolation service.
            config: Coordination configuration.

        Returns:
            Dispatch result with waves, workspaces, and phases.
        """
        ...


def _build_workspace_requests(
    routing_result: RoutingResult,
    config: CoordinationConfig,
) -> tuple[WorkspaceRequest, ...]:
    """Build workspace requests from routing decisions."""
    return tuple(
        WorkspaceRequest(
            task_id=d.subtask_id,
            agent_id=str(d.selected_candidate.agent_identity.id),
            base_branch=config.base_branch,
        )
        for d in routing_result.decisions
    )


def _validate_routing_against_decomposition(
    decomposition_result: DecompositionResult,
    routing_result: RoutingResult,
) -> None:
    """Validate all routed subtask IDs exist in created tasks.

    Must be called before workspace setup to avoid creating
    workspaces for nonexistent subtasks.

    Raises:
        CoordinationError: If a routed subtask has no created task.
    """
    created_ids = {t.id for t in decomposition_result.created_tasks}
    for decision in routing_result.decisions:
        if decision.subtask_id not in created_ids:
            msg = (
                f"Routed subtask {decision.subtask_id!r} has no "
                "corresponding created task in decomposition"
            )
            logger.warning(
                COORDINATION_PHASE_FAILED,
                phase="validate_routing",
                subtask_id=decision.subtask_id,
                error=msg,
            )
            raise CoordinationError(msg)


async def _setup_workspaces(
    workspace_service: WorkspaceIsolationService,
    routing_result: RoutingResult,
    config: CoordinationConfig,
) -> tuple[tuple[Workspace, ...], CoordinationPhaseResult]:
    """Set up workspaces and return them with a phase result."""
    start = time.monotonic()
    phase_name = "workspace_setup"

    logger.info(COORDINATION_PHASE_STARTED, phase=phase_name)
    try:
        requests = _build_workspace_requests(routing_result, config)
        workspaces = await workspace_service.setup_group(requests=requests)
    except MemoryError, RecursionError:
        # Bare re-raise: logging is intentionally omitted because
        # emitting logs may itself trigger MemoryError/RecursionError.
        # These are built-in exceptions (not synthorg.memory.errors.MemoryError).
        # Same pattern applies to all MemoryError/RecursionError guards
        # in this module.
        raise
    except Exception as exc:
        elapsed = time.monotonic() - start
        phase = CoordinationPhaseResult(
            phase=phase_name,
            success=False,
            duration_seconds=elapsed,
            error=str(exc),
        )
        logger.warning(
            COORDINATION_PHASE_FAILED,
            phase=phase_name,
            error=str(exc),
            exc_info=True,
        )
        return (), phase
    else:
        elapsed = time.monotonic() - start
        phase = CoordinationPhaseResult(
            phase=phase_name,
            success=True,
            duration_seconds=elapsed,
        )
        logger.info(
            COORDINATION_PHASE_COMPLETED,
            phase=phase_name,
            workspace_count=len(workspaces),
            duration_seconds=elapsed,
        )
        return workspaces, phase


async def _merge_workspaces(
    workspace_service: WorkspaceIsolationService,
    workspaces: tuple[Workspace, ...],
    *,
    phase_name: str = "merge",
) -> tuple[WorkspaceGroupResult | None, CoordinationPhaseResult]:
    """Merge workspaces and return result with a phase result."""
    start = time.monotonic()

    logger.info(COORDINATION_PHASE_STARTED, phase=phase_name)
    try:
        merge_result = await workspace_service.merge_group(
            workspaces=workspaces,
        )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        elapsed = time.monotonic() - start
        phase = CoordinationPhaseResult(
            phase=phase_name,
            success=False,
            duration_seconds=elapsed,
            error=str(exc),
        )
        logger.warning(
            COORDINATION_PHASE_FAILED,
            phase=phase_name,
            error=str(exc),
            exc_info=True,
        )
        return None, phase
    else:
        elapsed = time.monotonic() - start
        phase = CoordinationPhaseResult(
            phase=phase_name,
            success=True,
            duration_seconds=elapsed,
        )
        logger.info(
            COORDINATION_PHASE_COMPLETED,
            phase=phase_name,
            duration_seconds=elapsed,
        )
        return merge_result, phase


async def _teardown_workspaces(
    workspace_service: WorkspaceIsolationService,
    workspaces: tuple[Workspace, ...],
) -> None:
    """Best-effort teardown with logging."""
    logger.info(
        COORDINATION_CLEANUP_STARTED,
        workspace_count=len(workspaces),
    )
    try:
        await workspace_service.teardown_group(workspaces=workspaces)
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        logger.warning(
            COORDINATION_CLEANUP_FAILED,
            workspace_count=len(workspaces),
            error=str(exc),
            exc_info=True,
        )
    else:
        logger.info(
            COORDINATION_CLEANUP_COMPLETED,
            workspace_count=len(workspaces),
        )


async def _execute_waves(
    groups: tuple[ParallelExecutionGroup, ...],
    parallel_executor: ParallelExecutor,
    *,
    fail_fast: bool,
) -> tuple[list[CoordinationWave], list[CoordinationPhaseResult]]:
    """Execute wave groups sequentially, returning waves and phases."""
    waves: list[CoordinationWave] = []
    phases: list[CoordinationPhaseResult] = []

    for wave_idx, group in enumerate(groups):
        start = time.monotonic()
        phase_name = f"execute_wave_{wave_idx}"
        subtask_ids = tuple(a.task.id for a in group.assignments)

        logger.info(
            COORDINATION_WAVE_STARTED,
            wave_index=wave_idx,
            subtask_count=len(subtask_ids),
        )

        try:
            exec_result = await parallel_executor.execute_group(group)
            elapsed = time.monotonic() - start

            wave = CoordinationWave(
                wave_index=wave_idx,
                subtask_ids=subtask_ids,
                execution_result=exec_result,
            )
            waves.append(wave)

            success = exec_result.all_succeeded
            error_msg = (
                None
                if success
                else f"Wave {wave_idx}: {exec_result.agents_failed} agent(s) failed"
            )
            phases.append(
                CoordinationPhaseResult(
                    phase=phase_name,
                    success=success,
                    duration_seconds=elapsed,
                    error=error_msg,
                )
            )

            if success:
                logger.info(
                    COORDINATION_WAVE_COMPLETED,
                    wave_index=wave_idx,
                    succeeded=exec_result.agents_succeeded,
                    failed=exec_result.agents_failed,
                    duration_seconds=elapsed,
                )
            else:
                logger.warning(
                    COORDINATION_WAVE_COMPLETED,
                    wave_index=wave_idx,
                    succeeded=exec_result.agents_succeeded,
                    failed=exec_result.agents_failed,
                    duration_seconds=elapsed,
                )

            if not success and fail_fast:
                break

        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            elapsed = time.monotonic() - start
            logger.warning(
                COORDINATION_PHASE_FAILED,
                phase=phase_name,
                wave_index=wave_idx,
                error=str(exc),
                exc_info=True,
            )
            wave = CoordinationWave(
                wave_index=wave_idx,
                subtask_ids=subtask_ids,
            )
            waves.append(wave)
            phases.append(
                CoordinationPhaseResult(
                    phase=phase_name,
                    success=False,
                    duration_seconds=elapsed,
                    error=str(exc),
                )
            )
            if fail_fast:
                break

    return waves, phases


def _rebuild_group_with_workspaces(
    group: ParallelExecutionGroup,
    wave_workspaces: tuple[Workspace, ...],
) -> ParallelExecutionGroup:
    """Rebuild an execution group with workspace resource claims."""
    ws_lookup = {ws.task_id: ws.worktree_path for ws in wave_workspaces}
    new_assignments = tuple(
        a.model_copy(update={"resource_claims": (ws_lookup[a.task.id],)})
        if a.task.id in ws_lookup
        else a
        for a in group.assignments
    )
    return group.model_copy(update={"assignments": new_assignments})


class SasDispatcher:
    """SAS (Single-Agent-Step) dispatcher.

    Waves from DAG parallel groups. No workspace isolation.
    Designed for single-agent scenarios where the routing layer
    assigns all subtasks to one agent.
    """

    async def dispatch(
        self,
        *,
        decomposition_result: DecompositionResult,
        routing_result: RoutingResult,
        parallel_executor: ParallelExecutor,
        workspace_service: WorkspaceIsolationService | None,  # noqa: ARG002
        config: CoordinationConfig,
    ) -> DispatchResult:
        """Execute subtasks sequentially, one per wave."""
        groups = build_execution_waves(
            decomposition_result=decomposition_result,
            routing_result=routing_result,
            config=config,
        )

        waves, phases = await _execute_waves(
            groups, parallel_executor, fail_fast=config.fail_fast
        )

        return DispatchResult(
            waves=tuple(waves),
            phases=tuple(phases),
        )


class CentralizedDispatcher:
    """Centralized dispatcher.

    Waves from DAG parallel_groups(). Workspace isolation for all
    agents. Merge after all waves complete.
    """

    async def dispatch(
        self,
        *,
        decomposition_result: DecompositionResult,
        routing_result: RoutingResult,
        parallel_executor: ParallelExecutor,
        workspace_service: WorkspaceIsolationService | None,
        config: CoordinationConfig,
    ) -> DispatchResult:
        """Execute waves with workspace isolation and post-merge."""
        _validate_routing_against_decomposition(decomposition_result, routing_result)

        all_phases: list[CoordinationPhaseResult] = []
        workspaces: tuple[Workspace, ...] = ()
        merge_result: WorkspaceGroupResult | None = None

        # Setup workspaces if service available and isolation enabled
        if workspace_service is not None and config.enable_workspace_isolation:
            workspaces, setup_phase = await _setup_workspaces(
                workspace_service, routing_result, config
            )
            all_phases.append(setup_phase)
            if not setup_phase.success:
                return DispatchResult(phases=tuple(all_phases))

        try:
            groups = build_execution_waves(
                decomposition_result=decomposition_result,
                routing_result=routing_result,
                config=config,
                workspaces=workspaces,
            )

            waves, exec_phases = await _execute_waves(
                groups,
                parallel_executor,
                fail_fast=config.fail_fast,
            )
            all_phases.extend(exec_phases)

            # Merge only if all waves succeeded
            all_succeeded = all(p.success for p in exec_phases)
            if workspaces and workspace_service is not None and all_succeeded:
                merge_result, merge_phase = await _merge_workspaces(
                    workspace_service, workspaces
                )
                all_phases.append(merge_phase)
            elif workspaces and workspace_service is not None:
                logger.warning(
                    COORDINATION_PHASE_FAILED,
                    phase="merge",
                    error="Skipped merge: one or more waves failed",
                )

            return DispatchResult(
                waves=tuple(waves),
                workspaces=workspaces,
                workspace_merge=merge_result,
                phases=tuple(all_phases),
            )
        finally:
            if workspaces and workspace_service is not None:
                await _teardown_workspaces(workspace_service, workspaces)


class DecentralizedDispatcher:
    """Decentralized dispatcher.

    Waves from DAG parallel groups. Mandatory workspace isolation
    — raises ``CoordinationError`` if workspace service is
    unavailable or isolation is disabled.
    """

    async def dispatch(
        self,
        *,
        decomposition_result: DecompositionResult,
        routing_result: RoutingResult,
        parallel_executor: ParallelExecutor,
        workspace_service: WorkspaceIsolationService | None,
        config: CoordinationConfig,
    ) -> DispatchResult:
        """Execute subtasks with mandatory workspace isolation."""
        _validate_routing_against_decomposition(decomposition_result, routing_result)

        if workspace_service is None or not config.enable_workspace_isolation:
            msg = (
                "Decentralized topology requires workspace isolation "
                "but workspace_service is unavailable or isolation is disabled"
            )
            logger.warning(
                COORDINATION_PHASE_FAILED,
                phase="decentralized_precondition",
                error=msg,
            )
            raise CoordinationError(msg)

        all_phases: list[CoordinationPhaseResult] = []
        merge_result: WorkspaceGroupResult | None = None

        # Workspace setup is mandatory (guard above ensures both are available)
        workspaces, setup_phase = await _setup_workspaces(
            workspace_service, routing_result, config
        )
        all_phases.append(setup_phase)
        if not setup_phase.success:
            return DispatchResult(phases=tuple(all_phases))

        try:
            groups = build_execution_waves(
                decomposition_result=decomposition_result,
                routing_result=routing_result,
                config=config,
                workspaces=workspaces,
            )

            waves, exec_phases = await _execute_waves(
                groups,
                parallel_executor,
                fail_fast=config.fail_fast,
            )
            all_phases.extend(exec_phases)

            # Merge only if all waves succeeded
            all_succeeded = all(p.success for p in exec_phases)
            if workspaces and all_succeeded:
                merge_result, merge_phase = await _merge_workspaces(
                    workspace_service, workspaces
                )
                all_phases.append(merge_phase)
            elif workspaces:
                logger.warning(
                    COORDINATION_PHASE_FAILED,
                    phase="merge",
                    error="Skipped merge: one or more waves failed",
                )

            return DispatchResult(
                waves=tuple(waves),
                workspaces=workspaces,
                workspace_merge=merge_result,
                phases=tuple(all_phases),
            )
        finally:
            if workspaces:
                await _teardown_workspaces(workspace_service, workspaces)


class ContextDependentDispatcher:
    """Context-dependent dispatcher.

    Waves from DAG. Single-subtask waves skip isolation.
    Multi-subtask waves use workspace isolation with per-wave
    setup/merge.
    """

    async def dispatch(
        self,
        *,
        decomposition_result: DecompositionResult,
        routing_result: RoutingResult,
        parallel_executor: ParallelExecutor,
        workspace_service: WorkspaceIsolationService | None,
        config: CoordinationConfig,
    ) -> DispatchResult:
        """Execute waves with conditional workspace isolation."""
        groups = build_execution_waves(
            decomposition_result=decomposition_result,
            routing_result=routing_result,
            config=config,
        )

        all_phases: list[CoordinationPhaseResult] = []
        all_waves: list[CoordinationWave] = []
        all_workspaces: list[Workspace] = []
        merge_results: list[WorkspaceGroupResult] = []

        for wave_idx, group in enumerate(groups):
            wave_workspaces, exec_group = await self._setup_wave(
                wave_idx, group, workspace_service, config, all_phases, all_workspaces
            )
            if exec_group is None:
                if config.fail_fast:
                    break
                continue

            wave_failed = await self._execute_wave(
                wave_idx,
                exec_group,
                parallel_executor,
                all_waves,
                all_phases,
                wave_workspaces,
                workspace_service,
                merge_results,
            )

            if wave_failed and config.fail_fast:
                break

        return self._build_result(all_waves, all_workspaces, merge_results, all_phases)

    async def _setup_wave(  # noqa: PLR0913
        self,
        wave_idx: int,
        group: ParallelExecutionGroup,
        workspace_service: WorkspaceIsolationService | None,
        config: CoordinationConfig,
        all_phases: list[CoordinationPhaseResult],
        all_workspaces: list[Workspace],
    ) -> tuple[tuple[Workspace, ...], ParallelExecutionGroup | None]:
        """Set up workspaces for a wave if needed.

        Returns the wave's workspaces and the (possibly rebuilt) group,
        or ``None`` if setup failed.
        """
        needs_isolation = (
            len(group.assignments) > 1
            and workspace_service is not None
            and config.enable_workspace_isolation
        )

        if not needs_isolation:
            return (), group

        # Unreachable: narrowed by needs_isolation check above
        if workspace_service is None:  # pragma: no cover
            msg = "workspace_service required when isolation is enabled"
            raise CoordinationError(msg)

        wave_requests = tuple(
            WorkspaceRequest(
                task_id=a.task.id,
                agent_id=a.agent_id,
                base_branch=config.base_branch,
            )
            for a in group.assignments
        )
        ws_start = time.monotonic()
        try:
            wave_workspaces = await workspace_service.setup_group(
                requests=wave_requests,
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            ws_elapsed = time.monotonic() - ws_start
            logger.warning(
                COORDINATION_PHASE_FAILED,
                phase=f"workspace_setup_wave_{wave_idx}",
                error=str(exc),
                exc_info=True,
            )
            all_phases.append(
                CoordinationPhaseResult(
                    phase=f"workspace_setup_wave_{wave_idx}",
                    success=False,
                    duration_seconds=ws_elapsed,
                    error=str(exc),
                )
            )
            return (), None

        all_workspaces.extend(wave_workspaces)
        ws_elapsed = time.monotonic() - ws_start
        all_phases.append(
            CoordinationPhaseResult(
                phase=f"workspace_setup_wave_{wave_idx}",
                success=True,
                duration_seconds=ws_elapsed,
            )
        )

        rebuilt = _rebuild_group_with_workspaces(group, wave_workspaces)
        return wave_workspaces, rebuilt

    async def _execute_wave(  # noqa: PLR0913
        self,
        wave_idx: int,
        group: ParallelExecutionGroup,
        parallel_executor: ParallelExecutor,
        all_waves: list[CoordinationWave],
        all_phases: list[CoordinationPhaseResult],
        wave_workspaces: tuple[Workspace, ...],
        workspace_service: WorkspaceIsolationService | None,
        merge_results: list[WorkspaceGroupResult],
    ) -> bool:
        """Execute a single wave and handle per-wave merge/teardown.

        Returns:
            True if the wave failed, False if it succeeded.
        """
        start = time.monotonic()
        subtask_ids = tuple(a.task.id for a in group.assignments)
        wave_failed = False

        logger.info(
            COORDINATION_WAVE_STARTED,
            wave_index=wave_idx,
            subtask_count=len(subtask_ids),
        )

        try:
            exec_result = await parallel_executor.execute_group(group)
            elapsed = time.monotonic() - start

            all_waves.append(
                CoordinationWave(
                    wave_index=wave_idx,
                    subtask_ids=subtask_ids,
                    execution_result=exec_result,
                )
            )

            success = exec_result.all_succeeded
            wave_failed = not success
            error_msg = (
                None
                if success
                else f"Wave {wave_idx}: {exec_result.agents_failed} agent(s) failed"
            )
            all_phases.append(
                CoordinationPhaseResult(
                    phase=f"execute_wave_{wave_idx}",
                    success=success,
                    duration_seconds=elapsed,
                    error=error_msg,
                )
            )

            if success:
                logger.info(
                    COORDINATION_WAVE_COMPLETED,
                    wave_index=wave_idx,
                    succeeded=exec_result.agents_succeeded,
                    failed=exec_result.agents_failed,
                    duration_seconds=elapsed,
                )
            else:
                logger.warning(
                    COORDINATION_WAVE_COMPLETED,
                    wave_index=wave_idx,
                    succeeded=exec_result.agents_succeeded,
                    failed=exec_result.agents_failed,
                    duration_seconds=elapsed,
                )

        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            elapsed = time.monotonic() - start
            wave_failed = True
            logger.warning(
                COORDINATION_PHASE_FAILED,
                phase=f"execute_wave_{wave_idx}",
                wave_index=wave_idx,
                error=str(exc),
                exc_info=True,
            )
            all_waves.append(
                CoordinationWave(
                    wave_index=wave_idx,
                    subtask_ids=subtask_ids,
                )
            )
            all_phases.append(
                CoordinationPhaseResult(
                    phase=f"execute_wave_{wave_idx}",
                    success=False,
                    duration_seconds=elapsed,
                    error=str(exc),
                )
            )
        finally:
            if wave_workspaces and workspace_service is not None:
                # Only merge if the wave succeeded
                if not wave_failed:
                    merge_phase_name = f"merge_wave_{wave_idx}"
                    merge_result, merge_phase = await _merge_workspaces(
                        workspace_service,
                        wave_workspaces,
                        phase_name=merge_phase_name,
                    )
                    all_phases.append(merge_phase)
                    if merge_result is not None:
                        merge_results.append(merge_result)
                else:
                    logger.warning(
                        COORDINATION_PHASE_FAILED,
                        phase=f"merge_wave_{wave_idx}",
                        error="Skipped merge: wave failed",
                    )
                await _teardown_workspaces(workspace_service, wave_workspaces)

        return wave_failed

    @staticmethod
    def _build_result(
        all_waves: list[CoordinationWave],
        all_workspaces: list[Workspace],
        merge_results: list[WorkspaceGroupResult],
        all_phases: list[CoordinationPhaseResult],
    ) -> DispatchResult:
        """Combine wave and merge results into a DispatchResult."""
        combined_merge: WorkspaceGroupResult | None = None
        if merge_results:
            all_merge_results = tuple(
                mr for wgr in merge_results for mr in wgr.merge_results
            )
            total_merge_duration = sum(wgr.duration_seconds for wgr in merge_results)
            combined_merge = WorkspaceGroupResult(
                group_id="context-dependent-merge",
                merge_results=all_merge_results,
                duration_seconds=total_merge_duration,
            )

        return DispatchResult(
            waves=tuple(all_waves),
            workspaces=tuple(all_workspaces),
            workspace_merge=combined_merge,
            phases=tuple(all_phases),
        )


def select_dispatcher(topology: CoordinationTopology) -> TopologyDispatcher:
    """Select the appropriate dispatcher for a topology.

    Args:
        topology: The resolved coordination topology.

    Returns:
        A dispatcher instance for the topology.

    Raises:
        ValueError: If AUTO topology is passed (must be resolved first).
    """
    dispatcher: TopologyDispatcher
    match topology:
        case CoordinationTopology.SAS:
            dispatcher = SasDispatcher()
        case CoordinationTopology.CENTRALIZED:
            dispatcher = CentralizedDispatcher()
        case CoordinationTopology.DECENTRALIZED:
            dispatcher = DecentralizedDispatcher()
        case CoordinationTopology.CONTEXT_DEPENDENT:
            dispatcher = ContextDependentDispatcher()
        case _:
            msg = (
                f"Cannot dispatch topology {topology.value!r}: "
                "AUTO must be resolved before dispatch"
            )
            logger.warning(
                COORDINATION_PHASE_FAILED,
                phase="select_dispatcher",
                topology=topology.value,
                error=msg,
            )
            raise ValueError(msg)

    logger.debug(COORDINATION_TOPOLOGY_RESOLVED, topology=topology.value)
    return dispatcher
