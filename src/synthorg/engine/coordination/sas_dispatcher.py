"""SAS (Single-Agent-Step) dispatcher."""

from typing import TYPE_CHECKING

from synthorg.core.clock import Clock, SystemClock
from synthorg.engine.coordination._dispatch_helpers import execute_waves
from synthorg.engine.coordination.dispatcher_types import DispatchResult
from synthorg.engine.coordination.group_builder import build_execution_waves

if TYPE_CHECKING:
    from pathlib import Path

    from synthorg.core.types import NotBlankStr
    from synthorg.engine.coordination.config import CoordinationConfig
    from synthorg.engine.decomposition.models import DecompositionResult
    from synthorg.engine.parallel import ParallelExecutor
    from synthorg.engine.routing.models import RoutingResult
    from synthorg.engine.workspace.service import WorkspaceIsolationService


class SasDispatcher:
    """SAS (Single-Agent-Step) dispatcher.

    Waves from DAG parallel groups. No workspace isolation.
    Designed for single-agent scenarios where the routing layer
    assigns all subtasks to one agent.
    """

    def __init__(self, *, clock: Clock | None = None) -> None:
        self._clock: Clock = clock if clock is not None else SystemClock()

    async def dispatch(  # noqa: PLR0913 -- dispatch contract surface
        self,
        *,
        decomposition_result: DecompositionResult,
        routing_result: RoutingResult,
        parallel_executor: ParallelExecutor,
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

        waves, phases = await execute_waves(
            groups,
            parallel_executor,
            clock=self._clock,
            fail_fast=config.fail_fast,
        )

        return DispatchResult(
            waves=tuple(waves),
            phases=tuple(phases),
        )
