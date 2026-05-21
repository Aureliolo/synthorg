"""Context-dependent dispatcher."""

from typing import TYPE_CHECKING

from synthorg.core.clock import Clock, SystemClock
from synthorg.engine.coordination._dispatch_helpers import (
    merge_workspaces,
    rebuild_group_with_workspaces,
    teardown_workspaces,
    validate_routing_against_decomposition,
)
from synthorg.engine.coordination.dispatcher_types import DispatchResult
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
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.coordination import (
    COORDINATION_PHASE_FAILED,
    COORDINATION_WAVE_COMPLETED,
    COORDINATION_WAVE_STARTED,
)
from synthorg.observability.events.workspace import (
    WORKSPACE_SETUP_COMPLETE,
    WORKSPACE_SETUP_START,
)

if TYPE_CHECKING:
    from pathlib import Path

    from synthorg.core.types import NotBlankStr
    from synthorg.engine.coordination.config import CoordinationConfig
    from synthorg.engine.decomposition.models import DecompositionResult
    from synthorg.engine.parallel import ParallelExecutor
    from synthorg.engine.parallel_models import ParallelExecutionGroup
    from synthorg.engine.routing.models import RoutingResult
    from synthorg.engine.workspace.service import WorkspaceIsolationService

logger = get_logger(__name__)


class ContextDependentDispatcher:
    """Context-dependent dispatcher.

    Waves from DAG. Single-subtask waves skip isolation.
    Multi-subtask waves use workspace isolation with per-wave
    setup/merge.
    """

    def __init__(self, *, clock: Clock | None = None) -> None:
        self._clock: Clock = clock if clock is not None else SystemClock()

    async def dispatch(  # noqa: PLR0913 -- dispatch contract surface
        self,
        *,
        decomposition_result: DecompositionResult,
        routing_result: RoutingResult,
        parallel_executor: ParallelExecutor,
        workspace_service: WorkspaceIsolationService | None,
        config: CoordinationConfig,
        project_id: NotBlankStr | None = None,
        repo_root: Path | None = None,
    ) -> DispatchResult:
        """Execute waves with conditional workspace isolation."""
        validate_routing_against_decomposition(decomposition_result, routing_result)
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
                wave_idx,
                group,
                workspace_service,
                config,
                all_phases,
                all_workspaces,
                project_id=project_id,
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
                project_id=project_id,
                repo_root=repo_root,
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
        *,
        project_id: NotBlankStr | None = None,
    ) -> tuple[tuple[Workspace, ...], ParallelExecutionGroup | None]:
        """Set up workspaces for a wave if needed.

        Returns the wave's workspaces and the (possibly rebuilt) group,
        or ``None`` if setup failed.
        """
        needs_isolation = (
            len(group.assignments) > 1 and config.enable_workspace_isolation
        )

        if not needs_isolation:
            return (), group

        if workspace_service is None:
            msg = "workspace_service required when isolation is enabled"
            logger.warning(
                COORDINATION_PHASE_FAILED,
                phase="workspace_setup",
                error=msg,
            )
            raise CoordinationError(msg)

        wave_requests = tuple(
            WorkspaceRequest(
                task_id=a.task.id,
                agent_id=a.agent_id,
                base_branch=config.base_branch,
                project_id=project_id,
            )
            for a in group.assignments
        )
        logger.info(
            WORKSPACE_SETUP_START,
            wave_index=wave_idx,
            request_count=len(wave_requests),
        )
        ws_start = self._clock.monotonic()
        try:
            wave_workspaces = await workspace_service.setup_group(
                requests=wave_requests,
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            ws_elapsed = self._clock.monotonic() - ws_start
            logger.warning(
                COORDINATION_PHASE_FAILED,
                phase=f"workspace_setup_wave_{wave_idx}",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            all_phases.append(
                CoordinationPhaseResult(
                    phase=f"workspace_setup_wave_{wave_idx}",
                    success=False,
                    duration_seconds=ws_elapsed,
                    error=safe_error_description(exc),
                )
            )
            return (), None

        all_workspaces.extend(wave_workspaces)
        ws_elapsed = self._clock.monotonic() - ws_start
        logger.info(
            WORKSPACE_SETUP_COMPLETE,
            wave_index=wave_idx,
            workspace_count=len(wave_workspaces),
            duration_seconds=ws_elapsed,
        )
        all_phases.append(
            CoordinationPhaseResult(
                phase=f"workspace_setup_wave_{wave_idx}",
                success=True,
                duration_seconds=ws_elapsed,
            )
        )

        rebuilt = rebuild_group_with_workspaces(group, wave_workspaces)
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
        *,
        project_id: NotBlankStr | None = None,
        repo_root: Path | None = None,
    ) -> bool:
        """Execute a single wave and handle per-wave merge/teardown.

        Returns:
            True if the wave failed, False if it succeeded.
        """
        start = self._clock.monotonic()
        subtask_ids = tuple(a.task.id for a in group.assignments)
        wave_failed = False

        logger.info(
            COORDINATION_WAVE_STARTED,
            wave_index=wave_idx,
            subtask_count=len(subtask_ids),
        )

        try:
            exec_result = await parallel_executor.execute_group(group)
            elapsed = self._clock.monotonic() - start

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
            elapsed = self._clock.monotonic() - start
            wave_failed = True
            logger.warning(
                COORDINATION_PHASE_FAILED,
                phase=f"execute_wave_{wave_idx}",
                wave_index=wave_idx,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
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
                    error=safe_error_description(exc),
                )
            )
        finally:
            if wave_workspaces and workspace_service is not None:
                if not wave_failed:
                    merge_phase_name = f"merge_wave_{wave_idx}"
                    merge_result, merge_phase = await merge_workspaces(
                        workspace_service,
                        wave_workspaces,
                        clock=self._clock,
                        phase_name=merge_phase_name,
                        project_id=project_id,
                        repo_root=repo_root,
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
                await teardown_workspaces(workspace_service, wave_workspaces)

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
