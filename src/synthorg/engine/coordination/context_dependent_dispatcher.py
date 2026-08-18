"""Context-dependent dispatcher."""

from pathlib import Path

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.engine.coordination._dependency_gate import dependency_map
from synthorg.engine.coordination._dispatch_helpers import (
    merge_workspaces,
    rebuild_group_with_workspaces,
    teardown_workspaces,
    validate_routing_against_decomposition,
)
from synthorg.engine.coordination._wave_execution import execute_waves
from synthorg.engine.coordination._wave_outcome import WaveVerdict
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
from synthorg.engine.parallel_models import ParallelExecutionGroup
from synthorg.engine.parallel_protocol import ParallelExecutorProtocol
from synthorg.engine.routing.models import RoutingResult
from synthorg.engine.workspace.models import (
    Workspace,
    WorkspaceGroupResult,
    WorkspaceRequest,
)
from synthorg.engine.workspace.service import WorkspaceIsolationService
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.coordination import (
    COORDINATION_PHASE_FAILED,
    COORDINATION_WAVE_AWAITING_HUMAN,
)
from synthorg.observability.events.workspace import (
    WORKSPACE_SETUP_COMPLETE,
    WORKSPACE_SETUP_START,
)

logger = get_logger(__name__)


class ContextDependentDispatcher:
    """Context-dependent dispatcher.

    Waves from DAG. Single-subtask waves skip isolation.
    Multi-subtask waves use workspace isolation with per-wave
    setup/merge.

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
        workspace_service: WorkspaceIsolationService | None,
        config: CoordinationConfig,
        project_id: NotBlankStr | None = None,
        repo_root: Path | None = None,
    ) -> DispatchResult:
        """Execute waves with conditional workspace isolation.

        Returns:
            The :class:`DispatchResult` aggregating per-wave outcomes,
            allocated workspaces, merge outcomes, and phase metadata.
        """
        validate_routing_against_decomposition(decomposition_result, routing_result)
        groups = build_execution_waves(
            decomposition_result=decomposition_result,
            routing_result=routing_result,
            config=config,
        )

        all_waves: list[CoordinationWave] = []
        resources = _PerWaveWorkspaces(
            clock=self._clock,
            workspace_service=workspace_service,
            config=config,
            project_id=project_id,
            repo_root=repo_root,
        )
        all_phases = await execute_waves(
            groups,
            parallel_executor,
            clock=self._clock,
            fail_fast=config.fail_fast,
            assignment_writer=self._assignment_writer,
            waves=all_waves,
            dependencies=dependency_map(decomposition_result.plan.subtasks),
            resources=resources,
        )

        return self._build_result(
            all_waves,
            resources.allocated,
            resources.merges,
            all_phases,
        )

    @staticmethod
    def _build_result(
        all_waves: list[CoordinationWave],
        all_workspaces: list[Workspace],
        merge_results: list[WorkspaceGroupResult],
        all_phases: list[CoordinationPhaseResult],
    ) -> DispatchResult:
        """Combine wave and merge results into a DispatchResult.

        Returns:
            The aggregated :class:`DispatchResult` with combined
            per-wave merges flattened into a single
            ``workspace_merge`` summary entry.
        """
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


class _PerWaveWorkspaces:
    """Cut, merge and tear down one wave's worktrees at a time.

    This class owns the worktrees and nothing else. Gating, persistence,
    execution, classification and abandonment belong to the shared wave loop,
    which calls in here around each wave, so a rule about any of those has one
    place to be written.

    Args:
        clock: Injectable time source.
        workspace_service: Cuts and merges the worktrees. ``None`` leaves
            every wave unisolated.
        config: Whether isolation is enabled, and the branch to cut from.
        project_id: The project the worktrees belong to.
        repo_root: Where the merge lands.
    """

    def __init__(
        self,
        *,
        clock: Clock,
        workspace_service: WorkspaceIsolationService | None,
        config: CoordinationConfig,
        project_id: NotBlankStr | None,
        repo_root: Path | None,
    ) -> None:
        self._clock = clock
        self._workspace_service = workspace_service
        self._config = config
        self._project_id = project_id
        self._repo_root = repo_root
        #: Every worktree cut across the run, for the dispatch result.
        self.allocated: list[Workspace] = []
        #: One entry per wave that merged, flattened by the caller.
        self.merges: list[WorkspaceGroupResult] = []
        #: What each in-flight wave holds, so ``settle`` knows what to
        #: release without the shared loop having to carry it.
        self._held: dict[int, tuple[Workspace, ...]] = {}

    async def prepare(
        self,
        wave_idx: int,
        group: ParallelExecutionGroup,
        *,
        phases: list[CoordinationPhaseResult],
    ) -> ParallelExecutionGroup | None:
        """Cut this wave's worktrees and thread them into its assignments.

        Returns:
            The group rebuilt with workspace paths, unchanged when the wave
            needs no isolation, or ``None`` when the cut failed and the wave
            must not run.

        Raises:
            CoordinationError: When isolation is enabled but no
                ``workspace_service`` was provided (programmer error;
                signals a misconfigured pipeline).
        """
        # Recorded for every wave, including the ones holding nothing, so
        # ``settle`` can pop unconditionally and a wave that cut no worktrees
        # cannot inherit an earlier wave's.
        self._held[wave_idx] = ()
        needs_isolation = (
            len(group.assignments) > 1 and self._config.enable_workspace_isolation
        )
        if not needs_isolation:
            return group

        workspace_service = self._workspace_service
        if workspace_service is None:
            msg = "workspace_service required when isolation is enabled"
            logger.warning(
                COORDINATION_PHASE_FAILED,
                phase="workspace_setup",
                error=msg,
            )
            raise CoordinationError(msg)

        wave_workspaces = await self._cut_worktrees(
            wave_idx,
            group,
            workspace_service,
            phases=phases,
        )
        if wave_workspaces is None:
            return None
        return rebuild_group_with_workspaces(group, wave_workspaces)

    def _wave_requests(
        self,
        group: ParallelExecutionGroup,
    ) -> tuple[WorkspaceRequest, ...]:
        """Describe one worktree per assignment in *group*.

        Returns:
            The requests the workspace service cuts this wave's worktrees
            from, in assignment order.
        """
        return tuple(
            WorkspaceRequest(
                task_id=str(a.task.id),
                agent_id=a.agent_id,
                base_branch=self._config.base_branch,
                project_id=self._project_id,
            )
            for a in group.assignments
        )

    async def _cut_worktrees(
        self,
        wave_idx: int,
        group: ParallelExecutionGroup,
        workspace_service: WorkspaceIsolationService,
        *,
        phases: list[CoordinationPhaseResult],
    ) -> tuple[Workspace, ...] | None:
        """Cut this wave's worktrees, recording the attempt either way.

        Takes ownership on success: the workspaces are appended to
        ``allocated`` and held under *wave_idx* before returning, so a caller
        that fails afterwards still has them to release.

        Returns:
            The cut workspaces, or ``None`` when the cut failed and the wave
            must not run.
        """
        wave_requests = self._wave_requests(group)
        logger.info(
            WORKSPACE_SETUP_START,
            wave_index=wave_idx,
            request_count=len(wave_requests),
        )
        phase = f"workspace_setup_wave_{wave_idx}"
        ws_start = self._clock.monotonic()
        try:
            wave_workspaces = await workspace_service.setup_group(
                requests=wave_requests,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- best-effort side channel
            reraise_critical(exc)
            logger.warning(
                COORDINATION_PHASE_FAILED,
                phase=phase,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            phases.append(
                CoordinationPhaseResult(
                    phase=phase,
                    success=False,
                    duration_seconds=self._clock.monotonic() - ws_start,
                    error=safe_error_description(exc),
                )
            )
            return None

        self.allocated.extend(wave_workspaces)
        self._held[wave_idx] = wave_workspaces
        ws_elapsed = self._clock.monotonic() - ws_start
        logger.info(
            WORKSPACE_SETUP_COMPLETE,
            wave_index=wave_idx,
            workspace_count=len(wave_workspaces),
            duration_seconds=ws_elapsed,
        )
        phases.append(
            CoordinationPhaseResult(
                phase=phase,
                success=True,
                duration_seconds=ws_elapsed,
            )
        )
        return wave_workspaces

    async def settle(
        self,
        wave_idx: int,
        *,
        verdict: WaveVerdict,
        phases: list[CoordinationPhaseResult],
    ) -> None:
        """Merge and tear down what this wave held.

        A wave whose only non-successes are parks has not failed, and the
        workspace of a parked agent is kept: its run resumes there once the
        human decides, so tearing it down would leave the pending approval
        with nothing to resume into. That workspace is therefore neither
        merged (its work is mid-flight and unverified) nor torn down, which
        is what excluding the parked ids from ``settled`` buys.

        Only a genuine failure is reported as one. A wave that passed with
        every workspace parked also skips the merge, and calling that a
        failure would report one on the exact path a park is supposed to
        take.

        A real failure is recorded as a phase as well as logged, because the
        log does not survive the restart the question outlives and a rollup
        reads the phase list rather than the log: a level that emits no phase
        at all reads as still working rather than as failed. Its duration is
        zero because no merge was attempted -- the wave failed before this
        stage could start.
        """
        held = self._held.pop(wave_idx, ())
        workspace_service = self._workspace_service
        if not held or workspace_service is None:
            return
        settled = tuple(w for w in held if w.task_id not in verdict.parked_task_ids)
        if verdict.parked_task_ids:
            logger.info(
                COORDINATION_WAVE_AWAITING_HUMAN,
                wave_index=wave_idx,
                retained_workspaces=len(held) - len(settled),
            )
        try:
            if verdict.success and settled:
                merge_result, merge_phase = await merge_workspaces(
                    workspace_service,
                    settled,
                    clock=self._clock,
                    phase_name=f"merge_wave_{wave_idx}",
                    project_id=self._project_id,
                    repo_root=self._repo_root,
                )
                phases.append(merge_phase)
                if merge_result is not None:
                    self.merges.append(merge_result)
            elif verdict.failed:
                # Recorded as a phase as well as logged; the docstring says why.
                merge_error = verdict.error or "Skipped merge: wave failed"
                logger.warning(
                    COORDINATION_PHASE_FAILED,
                    phase=f"merge_wave_{wave_idx}",
                    error=merge_error,
                )
                phases.append(
                    CoordinationPhaseResult(
                        phase=f"merge_wave_{wave_idx}",
                        success=False,
                        duration_seconds=0.0,
                        error=merge_error,
                    )
                )
        finally:
            # A merge that raises leaves these workspaces held for the life of
            # the process: the wave has been popped from ``_held``, so nothing
            # else knows they exist, and each is a git worktree and a
            # container. The failure still propagates; it just does not take
            # the cleanup with it.
            if settled:
                await teardown_workspaces(workspace_service, settled)
