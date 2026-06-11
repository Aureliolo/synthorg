"""Shared helpers for topology dispatchers.

Private module holding workspace setup / merge / teardown,
wave execution, and validation primitives used by the four
concrete dispatchers.
"""

from pathlib import Path
from uuid import uuid4

from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.engine.coordination.config import CoordinationConfig
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
    MergeResult,
    Workspace,
    WorkspaceGroupResult,
    WorkspaceRequest,
)
from synthorg.engine.workspace.service import WorkspaceIsolationService
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.coordination import (
    COORDINATION_CLEANUP_COMPLETED,
    COORDINATION_CLEANUP_FAILED,
    COORDINATION_CLEANUP_STARTED,
    COORDINATION_PHASE_COMPLETED,
    COORDINATION_PHASE_FAILED,
    COORDINATION_PHASE_STARTED,
    COORDINATION_WAVE_COMPLETED,
    COORDINATION_WAVE_STARTED,
)

logger = get_logger(__name__)


def build_workspace_requests(
    routing_result: RoutingResult,
    config: CoordinationConfig,
    project_id: NotBlankStr | None = None,
) -> tuple[WorkspaceRequest, ...]:
    """Build workspace requests from routing decisions.

    Returns:
        A tuple of :class:`WorkspaceRequest` (one per routed subtask),
        carrying task id, agent id, base branch, and project id.
    """
    return tuple(
        WorkspaceRequest(
            task_id=d.subtask_id,
            agent_id=str(d.selected_candidate.agent_identity.id),
            base_branch=config.base_branch,
            project_id=project_id,
        )
        for d in routing_result.decisions
    )


def validate_routing_against_decomposition(
    decomposition_result: DecompositionResult,
    routing_result: RoutingResult,
) -> None:
    """Validate all routed subtask IDs exist in created tasks.

    Must be called before workspace setup to avoid creating
    workspaces for nonexistent subtasks.

    Raises:
        CoordinationError: If a routed subtask has no created task.
    """
    created_ids = {str(t.id) for t in decomposition_result.created_tasks}
    for decision in routing_result.decisions:
        if decision.subtask_id not in created_ids:
            msg = (
                f"Routed subtask {decision.subtask_id!r} has no "
                "corresponding created task in decomposition"
            )
            logger.warning(
                COORDINATION_PHASE_FAILED,
                phase="validate_routing",
                subtask_id=decision.subtask_id,
                error=msg,
            )
            raise CoordinationError(msg)


async def setup_workspaces(
    workspace_service: WorkspaceIsolationService,
    routing_result: RoutingResult,
    config: CoordinationConfig,
    *,
    clock: Clock,
    project_id: NotBlankStr | None = None,
) -> tuple[tuple[Workspace, ...], CoordinationPhaseResult]:
    """Set up workspaces and return them with a phase result.

    Returns:
        ``(workspaces, phase)`` where ``workspaces`` is the tuple of
        provisioned :class:`Workspace` (empty on failure) and ``phase``
        is the :class:`CoordinationPhaseResult` for the setup stage.
    """
    start = clock.monotonic()
    phase_name = "workspace_setup"

    logger.info(COORDINATION_PHASE_STARTED, phase=phase_name)
    try:
        requests = build_workspace_requests(routing_result, config, project_id)
        workspaces = await workspace_service.setup_group(requests=requests)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        elapsed = clock.monotonic() - start
        phase = CoordinationPhaseResult(
            phase=phase_name,
            success=False,
            duration_seconds=elapsed,
            # The error string is surfaced through
            # ``CoordinationPhaseResult`` to upstream consumers and
            # downstream logs; route through
            # ``safe_error_description`` so URL/form-body credentials
            # in HTTPStatusError-style messages are scrubbed at the
            # source.
            error=safe_error_description(exc),
        )
        logger.warning(
            COORDINATION_PHASE_FAILED,
            phase=phase_name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return (), phase
    else:
        elapsed = clock.monotonic() - start
        phase = CoordinationPhaseResult(
            phase=phase_name,
            success=True,
            duration_seconds=elapsed,
        )
        logger.info(
            COORDINATION_PHASE_COMPLETED,
            phase=phase_name,
            workspace_count=len(workspaces),
            duration_seconds=elapsed,
        )
        return workspaces, phase


async def _merge_group_via_push_queue(
    workspace_service: WorkspaceIsolationService,
    workspaces: tuple[Workspace, ...],
    *,
    project_id: NotBlankStr,
    repo_root: Path,
    clock: Clock,
) -> WorkspaceGroupResult:
    """Merge each workspace through the per-project serial push queue.

    Aggregates the per-workspace :class:`MergeResult`s into the same
    :class:`WorkspaceGroupResult` shape ``merge_group`` returns, so
    downstream consumers see no contract drift. The queue serialises
    the merge+push per project, exercising forge-collision safety end
    to end at runtime.

    Returns:
        A :class:`WorkspaceGroupResult` aggregating each workspace's
        :class:`MergeResult` and the total elapsed seconds.
    """
    start = clock.monotonic()
    results: list[MergeResult] = [
        await workspace_service.merge_workspace_with_push(
            workspace=workspace,
            project_id=project_id,
            repo_root=repo_root,
        )
        for workspace in workspaces
    ]
    elapsed = clock.monotonic() - start
    return WorkspaceGroupResult(
        group_id=str(uuid4()),
        merge_results=tuple(results),
        duration_seconds=elapsed,
    )


async def merge_workspaces(  # noqa: PLR0913 -- project/repo routing inputs
    workspace_service: WorkspaceIsolationService,
    workspaces: tuple[Workspace, ...],
    *,
    clock: Clock,
    phase_name: str = "merge",
    project_id: NotBlankStr | None = None,
    repo_root: Path | None = None,
) -> tuple[WorkspaceGroupResult | None, CoordinationPhaseResult]:
    """Merge workspaces and return result with a phase result.

    When *project_id* and *repo_root* are both supplied the merge runs
    through the per-project push queue (``merge_workspace_with_push``);
    otherwise it falls back to the in-memory ``merge_group``.

    Returns:
        ``(merge_result, phase)`` where ``merge_result`` is the
        :class:`WorkspaceGroupResult` on success, ``None`` on failure,
        and ``phase`` is the :class:`CoordinationPhaseResult` for the
        merge stage.
    """
    start = clock.monotonic()

    logger.info(COORDINATION_PHASE_STARTED, phase=phase_name)
    try:
        if project_id is not None and repo_root is not None:
            merge_result: (
                WorkspaceGroupResult | None
            ) = await _merge_group_via_push_queue(
                workspace_service,
                workspaces,
                project_id=project_id,
                repo_root=repo_root,
                clock=clock,
            )
        else:
            merge_result = await workspace_service.merge_group(
                workspaces=workspaces,
            )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        elapsed = clock.monotonic() - start
        phase = CoordinationPhaseResult(
            phase=phase_name,
            success=False,
            duration_seconds=elapsed,
            # Same scrub-at-source rationale as the earlier
            # ``setup_group`` failure handler.
            error=safe_error_description(exc),
        )
        logger.warning(
            COORDINATION_PHASE_FAILED,
            phase=phase_name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None, phase
    else:
        elapsed = clock.monotonic() - start
        phase = CoordinationPhaseResult(
            phase=phase_name,
            success=True,
            duration_seconds=elapsed,
        )
        logger.info(
            COORDINATION_PHASE_COMPLETED,
            phase=phase_name,
            duration_seconds=elapsed,
        )
        return merge_result, phase


async def teardown_workspaces(
    workspace_service: WorkspaceIsolationService,
    workspaces: tuple[Workspace, ...],
) -> None:
    """Best-effort teardown with logging."""
    logger.info(
        COORDINATION_CLEANUP_STARTED,
        workspace_count=len(workspaces),
    )
    try:
        await workspace_service.teardown_group(workspaces=workspaces)
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        reraise_critical(exc)
        logger.warning(
            COORDINATION_CLEANUP_FAILED,
            workspace_count=len(workspaces),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
    else:
        logger.info(
            COORDINATION_CLEANUP_COMPLETED,
            workspace_count=len(workspaces),
        )


async def execute_waves(
    groups: tuple[ParallelExecutionGroup, ...],
    parallel_executor: ParallelExecutorProtocol,
    *,
    clock: Clock,
    fail_fast: bool,
) -> tuple[list[CoordinationWave], list[CoordinationPhaseResult]]:
    """Execute wave groups sequentially, returning waves and phases.

    Returns:
        ``(waves, phases)`` where ``waves`` lists every executed
        :class:`CoordinationWave` (including failed ones with no
        ``execution_result``) and ``phases`` carries the matching
        :class:`CoordinationPhaseResult` for each wave.
    """
    waves: list[CoordinationWave] = []
    phases: list[CoordinationPhaseResult] = []

    for wave_idx, group in enumerate(groups):
        start = clock.monotonic()
        phase_name = f"execute_wave_{wave_idx}"
        subtask_ids = tuple(str(a.task.id) for a in group.assignments)

        logger.info(
            COORDINATION_WAVE_STARTED,
            wave_index=wave_idx,
            subtask_count=len(subtask_ids),
        )

        try:
            exec_result = await parallel_executor.execute_group(group)
            elapsed = clock.monotonic() - start

            wave = CoordinationWave(
                wave_index=wave_idx,
                subtask_ids=subtask_ids,
                execution_result=exec_result,
            )
            waves.append(wave)

            success = exec_result.all_succeeded
            error_msg = (
                None
                if success
                else f"Wave {wave_idx}: {exec_result.agents_failed} agent(s) failed"
            )
            phases.append(
                CoordinationPhaseResult(
                    phase=phase_name,
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

            if not success and fail_fast:
                break

        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            elapsed = clock.monotonic() - start
            logger.warning(
                COORDINATION_PHASE_FAILED,
                phase=phase_name,
                wave_index=wave_idx,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            wave = CoordinationWave(
                wave_index=wave_idx,
                subtask_ids=subtask_ids,
            )
            waves.append(wave)
            phases.append(
                CoordinationPhaseResult(
                    phase=phase_name,
                    success=False,
                    duration_seconds=elapsed,
                    # Same scrub-at-source rationale.
                    error=safe_error_description(exc),
                )
            )
            if fail_fast:
                break

    return waves, phases


def rebuild_group_with_workspaces(
    group: ParallelExecutionGroup,
    wave_workspaces: tuple[Workspace, ...],
) -> ParallelExecutionGroup:
    """Rebuild an execution group with workspace resource claims.

    Returns:
        A new :class:`ParallelExecutionGroup` whose assignments carry
        each subtask's workspace worktree path as a resource claim;
        assignments without a matching workspace are returned
        unchanged.
    """
    ws_lookup = {ws.task_id: ws.worktree_path for ws in wave_workspaces}
    new_assignments = tuple(
        a.model_copy(update={"resource_claims": (ws_lookup[str(a.task.id)],)})
        if str(a.task.id) in ws_lookup
        else a
        for a in group.assignments
    )
    return group.model_copy(update={"assignments": new_assignments})
