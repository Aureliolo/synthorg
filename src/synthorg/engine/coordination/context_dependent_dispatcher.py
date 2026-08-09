"""Context-dependent dispatcher."""

from pathlib import Path

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.engine.coordination._dispatch_helpers import (
    merge_workspaces,
    rebuild_group_with_workspaces,
    teardown_workspaces,
    validate_routing_against_decomposition,
)
from synthorg.engine.coordination._wave_outcome import WaveVerdict, classify_wave
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
    COORDINATION_WAVE_COMPLETED,
    COORDINATION_WAVE_STARTED,
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

        all_phases: list[CoordinationPhaseResult] = []
        all_waves: list[CoordinationWave] = []
        all_workspaces: list[Workspace] = []
        merge_results: list[WorkspaceGroupResult] = []

        for wave_idx, group in enumerate(groups):
            wave_workspaces, exec_group = await self._setup_wave(
                wave_idx,
                group,
                workspace_service=workspace_service,
                config=config,
                all_phases=all_phases,
                all_workspaces=all_workspaces,
                project_id=project_id,
            )
            if exec_group is None:
                if config.fail_fast:
                    break
                continue

            verdict = await self._execute_wave(
                wave_idx,
                exec_group,
                parallel_executor=parallel_executor,
                all_waves=all_waves,
                all_phases=all_phases,
                wave_workspaces=wave_workspaces,
                workspace_service=workspace_service,
                merge_results=merge_results,
                project_id=project_id,
                repo_root=repo_root,
            )

            if verdict.failed and config.fail_fast:
                break
            # A park is not subject to fail_fast: the wave has not finished,
            # and every wave after it was scheduled on the promise that it
            # had. Starting one now would read a half-written result as its
            # input.
            if verdict.parked_task_ids:
                logger.info(
                    COORDINATION_WAVE_AWAITING_HUMAN,
                    wave_index=wave_idx,
                    parked_tasks=len(verdict.parked_task_ids),
                    remaining_waves=len(groups) - wave_idx - 1,
                )
                break

        return self._build_result(all_waves, all_workspaces, merge_results, all_phases)

    async def _setup_wave(
        self,
        wave_idx: int,
        group: ParallelExecutionGroup,
        *,
        workspace_service: WorkspaceIsolationService | None,
        config: CoordinationConfig,
        all_phases: list[CoordinationPhaseResult],
        all_workspaces: list[Workspace],
        project_id: NotBlankStr | None = None,
    ) -> tuple[tuple[Workspace, ...], ParallelExecutionGroup | None]:
        """Set up workspaces for a wave if needed.

        Returns:
            ``(workspaces, rebuilt_group)`` on success; the rebuilt
            group has workspace paths threaded into each assignment.
            ``(workspaces, None)`` when the wave needs isolation but
            setup failed (the caller decides whether to ``fail_fast``).

        Raises:
            CoordinationError: When isolation is enabled but no
                ``workspace_service`` was provided (programmer error;
                signals a misconfigured pipeline).
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
                task_id=str(a.task.id),
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
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- best-effort side channel
            reraise_critical(exc)
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
        *,
        parallel_executor: ParallelExecutorProtocol,
        all_waves: list[CoordinationWave],
        all_phases: list[CoordinationPhaseResult],
        wave_workspaces: tuple[Workspace, ...],
        workspace_service: WorkspaceIsolationService | None,
        merge_results: list[WorkspaceGroupResult],
        project_id: NotBlankStr | None = None,
        repo_root: Path | None = None,
    ) -> WaveVerdict:
        """Execute a single wave and handle per-wave merge/teardown.

        A wave whose only non-successes are parks has not failed, and the
        workspace of a parked agent is kept: its run resumes there once the
        human decides, so tearing it down would leave the pending approval
        with nothing to resume into.

        Returns:
            The wave's :class:`WaveVerdict`. The caller reads
            ``blocks_dependents`` to decide whether the waves after this one
            may run at all.
        """
        start = self._clock.monotonic()
        subtask_ids = tuple(str(a.task.id) for a in group.assignments)
        # Failed until the wave earns otherwise. A cancellation is a
        # BaseException and skips every ``except Exception`` below, so a flag
        # cleared on the success path is the one thing an unwind cannot
        # reach: starting here means an interrupted wave never takes the
        # merge-and-push branch in the ``finally``.
        verdict = WaveVerdict(failed=True, error=f"Wave {wave_idx}: did not finish")

        logger.info(
            COORDINATION_WAVE_STARTED,
            wave_index=wave_idx,
            subtask_count=len(subtask_ids),
        )

        try:
            assigned = await self._assignment_writer.persist(group)
            exec_result = await parallel_executor.execute_group(assigned)
            elapsed = self._clock.monotonic() - start
            verdict = classify_wave(wave_idx, exec_result)

            all_waves.append(
                CoordinationWave(
                    wave_index=wave_idx,
                    subtask_ids=subtask_ids,
                    execution_result=exec_result,
                )
            )
            all_phases.append(
                CoordinationPhaseResult(
                    phase=f"execute_wave_{wave_idx}",
                    success=verdict.success,
                    duration_seconds=elapsed,
                    error=verdict.error,
                )
            )

            log = logger.info if verdict.success else logger.warning
            log(
                COORDINATION_WAVE_COMPLETED,
                wave_index=wave_idx,
                succeeded=exec_result.agents_succeeded,
                failed=exec_result.agents_failed,
                awaiting_human=exec_result.agents_awaiting_human,
                duration_seconds=elapsed,
            )

        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- best-effort side channel
            reraise_critical(exc)
            elapsed = self._clock.monotonic() - start
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
                # A parked run resumes into its own workspace, so that
                # workspace is neither merged (its work is mid-flight and
                # unverified) nor torn down (the resume needs it).
                settled = tuple(
                    w
                    for w in wave_workspaces
                    if w.task_id not in verdict.parked_task_ids
                )
                if verdict.parked_task_ids:
                    logger.info(
                        COORDINATION_WAVE_AWAITING_HUMAN,
                        wave_index=wave_idx,
                        retained_workspaces=len(wave_workspaces) - len(settled),
                    )
                if verdict.success and settled:
                    merge_phase_name = f"merge_wave_{wave_idx}"
                    merge_result, merge_phase = await merge_workspaces(
                        workspace_service,
                        settled,
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
                        error=verdict.error or "Skipped merge: wave failed",
                    )
                if settled:
                    await teardown_workspaces(workspace_service, settled)

        return verdict

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
