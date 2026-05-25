"""Centralized dispatcher."""

from typing import TYPE_CHECKING

from synthorg.core.clock import Clock, SystemClock
from synthorg.engine.coordination._dispatch_helpers import (
    execute_waves,
    merge_workspaces,
    setup_workspaces,
    teardown_workspaces,
    validate_routing_against_decomposition,
)
from synthorg.engine.coordination.dispatcher_types import DispatchResult
from synthorg.engine.coordination.group_builder import build_execution_waves
from synthorg.observability import get_logger
from synthorg.observability.events.coordination import COORDINATION_PHASE_FAILED

if TYPE_CHECKING:
    from pathlib import Path

    from synthorg.core.types import NotBlankStr
    from synthorg.engine.coordination.config import CoordinationConfig
    from synthorg.engine.coordination.models import CoordinationPhaseResult
    from synthorg.engine.decomposition.models import DecompositionResult
    from synthorg.engine.parallel import ParallelExecutor
    from synthorg.engine.routing.models import RoutingResult
    from synthorg.engine.workspace.models import Workspace, WorkspaceGroupResult
    from synthorg.engine.workspace.service import WorkspaceIsolationService

logger = get_logger(__name__)


class CentralizedDispatcher:
    """Centralized dispatcher.

    Waves from DAG parallel_groups(). Workspace isolation for all
    agents. Merge after all waves complete.
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
        """Execute waves with workspace isolation and post-merge.

        Returns:
            A :class:`DispatchResult` aggregating per-wave outcomes,
            allocated workspaces, the workspace merge result, and
            phase metadata.
        """
        validate_routing_against_decomposition(decomposition_result, routing_result)

        all_phases: list[CoordinationPhaseResult] = []
        workspaces: tuple[Workspace, ...] = ()
        merge_result: WorkspaceGroupResult | None = None

        if workspace_service is not None and config.enable_workspace_isolation:
            workspaces, setup_phase = await setup_workspaces(
                workspace_service,
                routing_result,
                config,
                clock=self._clock,
                project_id=project_id,
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

            waves, exec_phases = await execute_waves(
                groups,
                parallel_executor,
                clock=self._clock,
                fail_fast=config.fail_fast,
            )
            all_phases.extend(exec_phases)

            all_succeeded = all(p.success for p in exec_phases)
            if workspaces and workspace_service is not None and all_succeeded:
                merge_result, merge_phase = await merge_workspaces(
                    workspace_service,
                    workspaces,
                    clock=self._clock,
                    project_id=project_id,
                    repo_root=repo_root,
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
                await teardown_workspaces(workspace_service, workspaces)
