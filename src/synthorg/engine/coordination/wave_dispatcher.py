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
from synthorg.engine.coordination._dependency_gate import dependency_map
from synthorg.engine.coordination._dispatch_helpers import (
    merge_workspaces,
    setup_workspaces,
    teardown_workspaces,
    validate_routing_against_decomposition,
)
from synthorg.engine.coordination._wave_execution import execute_waves
from synthorg.engine.coordination._wave_outcome import parked_tasks
from synthorg.engine.coordination.assignment_writer import AssignmentWriter
from synthorg.engine.coordination.config import CoordinationConfig
from synthorg.engine.coordination.dispatcher_types import DispatchResult
from synthorg.engine.coordination.group_builder import build_execution_waves
from synthorg.engine.coordination.models import (
    CoordinationPhaseResult,
    CoordinationWave,
)
from synthorg.engine.decomposition.models import DecompositionResult
from synthorg.engine.errors import CoordinationError
from synthorg.engine.parallel_protocol import ParallelExecutorProtocol
from synthorg.engine.routing.models import RoutingResult
from synthorg.engine.workspace.models import Workspace, WorkspaceGroupResult
from synthorg.engine.workspace.service import WorkspaceIsolationService
from synthorg.observability import get_logger
from synthorg.observability.events.coordination import (
    COORDINATION_ISOLATION_DEGRADED,
    COORDINATION_PHASE_FAILED,
    COORDINATION_WAVE_AWAITING_HUMAN,
)
from synthorg.observability.tracing.instrumentation import get_tracer

logger = get_logger(__name__)
_tracer = get_tracer(__name__)


class WaveDispatcher:
    """Execute DAG waves, optionally under per-agent workspace isolation.

    Args:
        clock: Injectable time source.
        isolation_required: When ``True`` (decentralized topology), a
            missing workspace service, disabled isolation, or a failed
            setup raises ``CoordinationError``; when ``False``
            (centralized topology), isolation is best-effort and the
            waves run unisolated when it is unavailable or setup fails.
        topology_label: Topology name carried for the precondition
            message and the ``COORDINATION_PHASE_FAILED`` phase tag.
        assignment_writer: Persists each wave's assignments through the
            central engine before that wave runs. ``None`` builds an
            engine-less writer, which passes the wave through unchanged.
    """

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        isolation_required: bool,
        topology_label: str,
        assignment_writer: AssignmentWriter | None = None,
    ) -> None:
        self._clock: Clock = clock if clock is not None else SystemClock()
        self._isolation_required = isolation_required
        self._topology_label = topology_label
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
            CoordinationError: When ``isolation_required`` is set and the
                workspace service is missing, isolation is disabled, or
                its setup fails.
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
        # Filled wave by wave rather than returned at the end, so the
        # teardown below knows who parked even when a cancellation unwinds
        # the dispatch mid-run. Read from the waves themselves and not from
        # a separate flag, because a flag set on the success path is exactly
        # what a BaseException skips.
        waves: list[CoordinationWave] = []

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
                self._on_setup_failed(setup_phase.error)

        try:
            groups = build_execution_waves(
                decomposition_result=decomposition_result,
                routing_result=routing_result,
                config=config,
                workspaces=workspaces,
            )

            with _tracer.start_as_current_span(
                "coordination.dispatch",
                attributes={
                    "coordination.topology": self._topology_label,
                    "coordination.wave_count": len(groups),
                },
                record_exception=False,
                set_status_on_exception=False,
            ):
                exec_phases = await execute_waves(
                    groups,
                    parallel_executor,
                    clock=self._clock,
                    fail_fast=config.fail_fast,
                    assignment_writer=self._assignment_writer,
                    waves=waves,
                    dependencies=dependency_map(decomposition_result.plan.subtasks),
                )
            all_phases.extend(exec_phases)

            all_succeeded = all(p.success for p in exec_phases)
            # A parked run resumes into its own workspace, so that workspace
            # is neither merged (its work is mid-flight and unverified) nor
            # torn down (the resume needs it).
            parked_task_ids = parked_tasks(waves)
            settled = tuple(w for w in workspaces if w.task_id not in parked_task_ids)
            if parked_task_ids:
                logger.info(
                    COORDINATION_WAVE_AWAITING_HUMAN,
                    retained_workspaces=len(workspaces) - len(settled),
                )
            if settled and workspace_service is not None and all_succeeded:
                merge_result, merge_phase = await merge_workspaces(
                    workspace_service,
                    settled,
                    clock=self._clock,
                    project_id=project_id,
                    repo_root=repo_root,
                )
                all_phases.append(merge_phase)
            elif workspaces and workspace_service is not None and not all_succeeded:
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
            if workspace_service is not None:
                torn_down = tuple(
                    w for w in workspaces if w.task_id not in parked_tasks(waves)
                )
                if torn_down:
                    await teardown_workspaces(workspace_service, torn_down)

    def _on_setup_failed(self, detail: str | None) -> None:
        """Decide what a failed workspace setup means for this topology.

        Each topology reports what the failure means for it. Mandatory
        isolation is a precondition, and a precondition that failed is a
        dispatch failure. Best-effort isolation is the case the flag
        exists for, so the waves run unisolated rather than not at all.

        Neither may answer with an empty ``DispatchResult``: upstream
        that reads as "dispatched nothing, successfully", because the
        rollup sees subtasks that never ran, no wave carries an error,
        and the coordination-metrics collector finds nothing to collect.

        Args:
            detail: The setup phase's description of what went wrong,
                already redacted by ``safe_error_description`` where the
                phase was built.

        Raises:
            CoordinationError: When this topology mandates isolation.
        """
        if self._isolation_required:
            msg = (
                f"{self._topology_label.capitalize()} topology requires "
                f"workspace isolation and its setup failed: {detail}"
            )
            logger.warning(
                COORDINATION_PHASE_FAILED,
                phase=f"{self._topology_label}_precondition",
                error=msg,
            )
            raise CoordinationError(msg)
        logger.warning(
            COORDINATION_ISOLATION_DEGRADED,
            topology=self._topology_label,
            detail=detail,
        )
