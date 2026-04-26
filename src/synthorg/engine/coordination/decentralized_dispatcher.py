"""Decentralized dispatcher."""

from typing import TYPE_CHECKING

from synthorg.engine.coordination._dispatch_helpers import (
    execute_waves,
    merge_workspaces,
    setup_workspaces,
    teardown_workspaces,
    validate_routing_against_decomposition,
)
from synthorg.engine.coordination.dispatcher_types import DispatchResult
from synthorg.engine.coordination.group_builder import build_execution_waves
from synthorg.engine.errors import CoordinationError
from synthorg.observability import get_logger
from synthorg.observability.events.coordination import COORDINATION_PHASE_FAILED

if TYPE_CHECKING:
    from synthorg.engine.coordination.config import CoordinationConfig
    from synthorg.engine.coordination.models import CoordinationPhaseResult
    from synthorg.engine.decomposition.models import DecompositionResult
    from synthorg.engine.parallel import ParallelExecutor
    from synthorg.engine.routing.models import RoutingResult
    from synthorg.engine.workspace.models import WorkspaceGroupResult
    from synthorg.engine.workspace.service import WorkspaceIsolationService

logger = get_logger(__name__)


class DecentralizedDispatcher:
    """Decentralized dispatcher.

    Waves from DAG parallel groups. Mandatory workspace isolation:
    raises ``CoordinationError`` if workspace service is
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
        validate_routing_against_decomposition(decomposition_result, routing_result)

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

        workspaces, setup_phase = await setup_workspaces(
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

            waves, exec_phases = await execute_waves(
                groups,
                parallel_executor,
                fail_fast=config.fail_fast,
            )
            all_phases.extend(exec_phases)

            all_succeeded = all(p.success for p in exec_phases)
            if workspaces and all_succeeded:
                merge_result, merge_phase = await merge_workspaces(
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
                await teardown_workspaces(workspace_service, workspaces)
