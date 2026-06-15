"""Wave dispatcher: DAG-wave execution with optional workspace isolation.

One dispatcher for both the centralized and decentralized coordination
topologies. They share the entire validate -> setup -> waves -> merge ->
teardown body via ``_dispatch_helpers``; the only difference is whether
workspace isolation is mandatory (``isolation_required``). The
topology label is data, carried only for the precondition message and
phase name.
"""

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
from synthorg.engine.workspace.models import Workspace, WorkspaceGroupResult
from synthorg.engine.workspace.service import WorkspaceIsolationService
from synthorg.observability import get_logger
from synthorg.observability.events.coordination import COORDINATION_PHASE_FAILED

logger = get_logger(__name__)


class WaveDispatcher:
    """Execute DAG waves, optionally under per-agent workspace isolation.

    Args:
        clock: Injectable time source.
        isolation_required: When ``True`` (decentralized topology), a
            missing workspace service or disabled isolation raises
            ``CoordinationError``; when ``False`` (centralized topology),
            isolation is best-effort and skipped when unavailable.
        topology_label: Topology name carried for the precondition
            message and the ``COORDINATION_PHASE_FAILED`` phase tag.
    """

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        isolation_required: bool,
        topology_label: str,
    ) -> None:
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._isolation_required = isolation_required
        self._topology_label = topology_label

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
        """Execute waves, isolating workspaces per the topology contract.

        Returns:
            A :class:`DispatchResult` aggregating per-wave outcomes,
            allocated workspaces, the workspace merge result, and phase
            metadata.

        Raises:
            CoordinationError: When ``isolation_required`` is set but the
                workspace service is missing or isolation is disabled.
        """
        validate_routing_against_decomposition(decomposition_result, routing_result)

        isolation_active = (
            workspace_service is not None and config.enable_workspace_isolation
        )
        if self._isolation_required and not isolation_active:
            msg = (
                f"{self._topology_label.capitalize()} topology requires "
                "workspace isolation but workspace_service is unavailable "
                "or isolation is disabled"
            )
            logger.warning(
                COORDINATION_PHASE_FAILED,
                phase=f"{self._topology_label}_precondition",
                error=msg,
            )
            raise CoordinationError(msg)

        all_phases: list[CoordinationPhaseResult] = []
        workspaces: tuple[Workspace, ...] = ()
        merge_result: WorkspaceGroupResult | None = None

        if isolation_active and workspace_service is not None:
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
