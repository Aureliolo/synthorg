"""SAS (Single-Agent-Step) dispatcher."""

from pathlib import Path

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.types import NotBlankStr
from synthorg.engine.coordination._dependency_gate import dependency_map
from synthorg.engine.coordination._wave_execution import execute_waves
from synthorg.engine.coordination.assignment_writer import AssignmentWriter
from synthorg.engine.coordination.config import CoordinationConfig
from synthorg.engine.coordination.dispatcher_types import DispatchResult
from synthorg.engine.coordination.group_builder import build_execution_waves
from synthorg.engine.coordination.models import CoordinationWave
from synthorg.engine.decomposition.models import DecompositionResult
from synthorg.engine.parallel_protocol import ParallelExecutorProtocol
from synthorg.engine.routing.models import RoutingResult
from synthorg.engine.workspace.service import WorkspaceIsolationService


class SasDispatcher:
    """SAS (Single-Agent-Step) dispatcher.

    Waves from DAG parallel groups. No workspace isolation.
    Designed for single-agent scenarios where the routing layer
    assigns all subtasks to one agent.

    Args:
        clock: Injectable time source.
        assignment_writer: Persists each wave's assignments through the
            central engine before that wave runs. ``None`` builds an
            engine-less writer, which passes the wave through unchanged.
    """

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        assignment_writer: AssignmentWriter | None = None,
    ) -> None:
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._assignment_writer = (
            assignment_writer
            if assignment_writer is not None
            else AssignmentWriter(None)
        )

    async def dispatch(
        self,
        *,
        decomposition_result: DecompositionResult,
        routing_result: RoutingResult,
        parallel_executor: ParallelExecutorProtocol,
        workspace_service: WorkspaceIsolationService | None,  # noqa: ARG002
        config: CoordinationConfig,
        project_id: NotBlankStr | None = None,  # noqa: ARG002 -- no isolation in SAS
        repo_root: Path | None = None,  # noqa: ARG002 -- no isolation in SAS
    ) -> DispatchResult:
        """Execute subtasks sequentially, one per wave.

        Returns:
            A :class:`DispatchResult` carrying per-wave outcomes and
            phase metadata; SAS topology does not use workspaces or
            per-merge result.
        """
        groups = build_execution_waves(
            decomposition_result=decomposition_result,
            routing_result=routing_result,
            config=config,
        )

        waves: list[CoordinationWave] = []
        phases = await execute_waves(
            groups,
            parallel_executor,
            clock=self._clock,
            fail_fast=config.fail_fast,
            assignment_writer=self._assignment_writer,
            waves=waves,
            dependencies=dependency_map(decomposition_result.plan.subtasks),
        )

        return DispatchResult(
            waves=tuple(waves),
            phases=tuple(phases),
        )
