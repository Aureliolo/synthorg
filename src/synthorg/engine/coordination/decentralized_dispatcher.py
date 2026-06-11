"""Decentralized dispatcher."""

from pathlib import Path

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.types import NotBlankStr
from synthorg.engine.coordination._dispatch_helpers import (
    execute_waves,
    merge_workspaces,
    setup_workspaces,
    teardown_workspaces,
    validate_routing_against_decomposition,
)
from synthorg.engine.coordination.config import CoordinationConfig
from synthorg.engine.coordination.dispatcher_types import DispatchResult
from synthorg.engine.coordination.group_builder import build_execution_waves
from synthorg.engine.coordination.models import CoordinationPhaseResult
from synthorg.engine.decomposition.models import DecompositionResult
from synthorg.engine.errors import CoordinationError
from synthorg.engine.parallel_protocol import ParallelExecutorProtocol
from synthorg.engine.routing.models import RoutingResult
from synthorg.engine.workspace.models import WorkspaceGroupResult
from synthorg.engine.workspace.service import WorkspaceIsolationService
from synthorg.observability import get_logger
from synthorg.observability.events.coordination import COORDINATION_PHASE_FAILED

logger = get_logger(__name__)


class DecentralizedDispatcher:
    """Decentralized dispatcher.

    Waves from DAG parallel groups. Mandatory workspace isolation:
    raises ``CoordinationError`` if workspace service is
    unavailable or isolation is disabled.
    """

    def __init__(self, *, clock: Clock | None = None) -> None:
        self._clock: Clock = clock if clock is not None else SystemClock()

    async def dispatch(  # noqa: PLR0913 -- dispatch contract surface
        self,
        *,
        decomposition_result: DecompositionResult,
        routing_result: RoutingResult,
        parallel_executor: ParallelExecutorProtocol,
        workspace_service: WorkspaceIsolationService | None,
        config: CoordinationConfig,
        project_id: NotBlankStr | None = None,
        repo_root: Path | None = None,
    ) -> DispatchResult:
        """Execute subtasks with mandatory workspace isolation.

        Returns:
            A :class:`DispatchResult` aggregating per-wave outcomes,
            isolated workspaces, the per-agent merge results, and
            phase metadata.

        Raises:
            CoordinationError: When the workspace service is missing
                or workspace isolation is disabled (decentralized
                topology cannot operate without per-agent isolation).
        """
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
            if workspaces and all_succeeded:
                merge_result, merge_phase = await merge_workspaces(
                    workspace_service,
                    workspaces,
                    clock=self._clock,
                    project_id=project_id,
                    repo_root=repo_root,
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
