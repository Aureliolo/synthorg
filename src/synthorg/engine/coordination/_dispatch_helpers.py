"""Shared helpers for topology dispatchers.

Private module holding workspace setup / merge / teardown,
wave execution, and validation primitives used by the four
concrete dispatchers.
"""

import time
from typing import TYPE_CHECKING

from synthorg.engine.coordination.models import (
    CoordinationPhaseResult,
    CoordinationWave,
)
from synthorg.engine.errors import CoordinationError
from synthorg.engine.workspace.models import (
    Workspace,
    WorkspaceGroupResult,
    WorkspaceRequest,
)
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

if TYPE_CHECKING:
    from synthorg.engine.coordination.config import CoordinationConfig
    from synthorg.engine.decomposition.models import DecompositionResult
    from synthorg.engine.parallel import ParallelExecutor
    from synthorg.engine.parallel_models import ParallelExecutionGroup
    from synthorg.engine.routing.models import RoutingResult
    from synthorg.engine.workspace.service import WorkspaceIsolationService

logger = get_logger(__name__)


def build_workspace_requests(
    routing_result: RoutingResult,
    config: CoordinationConfig,
) -> tuple[WorkspaceRequest, ...]:
    """Build workspace requests from routing decisions."""
    return tuple(
        WorkspaceRequest(
            task_id=d.subtask_id,
            agent_id=str(d.selected_candidate.agent_identity.id),
            base_branch=config.base_branch,
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
    created_ids = {t.id for t in decomposition_result.created_tasks}
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
) -> tuple[tuple[Workspace, ...], CoordinationPhaseResult]:
    """Set up workspaces and return them with a phase result."""
    start = time.monotonic()
    phase_name = "workspace_setup"

    logger.info(COORDINATION_PHASE_STARTED, phase=phase_name)
    try:
        requests = build_workspace_requests(routing_result, config)
        workspaces = await workspace_service.setup_group(requests=requests)
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        elapsed = time.monotonic() - start
        phase = CoordinationPhaseResult(
            phase=phase_name,
            success=False,
            duration_seconds=elapsed,
            error=str(exc),
        )
        logger.warning(
            COORDINATION_PHASE_FAILED,
            phase=phase_name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return (), phase
    else:
        elapsed = time.monotonic() - start
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


async def merge_workspaces(
    workspace_service: WorkspaceIsolationService,
    workspaces: tuple[Workspace, ...],
    *,
    phase_name: str = "merge",
) -> tuple[WorkspaceGroupResult | None, CoordinationPhaseResult]:
    """Merge workspaces and return result with a phase result."""
    start = time.monotonic()

    logger.info(COORDINATION_PHASE_STARTED, phase=phase_name)
    try:
        merge_result = await workspace_service.merge_group(
            workspaces=workspaces,
        )
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
        elapsed = time.monotonic() - start
        phase = CoordinationPhaseResult(
            phase=phase_name,
            success=False,
            duration_seconds=elapsed,
            error=str(exc),
        )
        logger.warning(
            COORDINATION_PHASE_FAILED,
            phase=phase_name,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return None, phase
    else:
        elapsed = time.monotonic() - start
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
    except MemoryError, RecursionError:
        raise
    except Exception as exc:
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
    parallel_executor: ParallelExecutor,
    *,
    fail_fast: bool,
) -> tuple[list[CoordinationWave], list[CoordinationPhaseResult]]:
    """Execute wave groups sequentially, returning waves and phases."""
    waves: list[CoordinationWave] = []
    phases: list[CoordinationPhaseResult] = []

    for wave_idx, group in enumerate(groups):
        start = time.monotonic()
        phase_name = f"execute_wave_{wave_idx}"
        subtask_ids = tuple(a.task.id for a in group.assignments)

        logger.info(
            COORDINATION_WAVE_STARTED,
            wave_index=wave_idx,
            subtask_count=len(subtask_ids),
        )

        try:
            exec_result = await parallel_executor.execute_group(group)
            elapsed = time.monotonic() - start

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

        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            elapsed = time.monotonic() - start
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
                    error=str(exc),
                )
            )
            if fail_fast:
                break

    return waves, phases


def rebuild_group_with_workspaces(
    group: ParallelExecutionGroup,
    wave_workspaces: tuple[Workspace, ...],
) -> ParallelExecutionGroup:
    """Rebuild an execution group with workspace resource claims."""
    ws_lookup = {ws.task_id: ws.worktree_path for ws in wave_workspaces}
    new_assignments = tuple(
        a.model_copy(update={"resource_claims": (ws_lookup[a.task.id],)})
        if a.task.id in ws_lookup
        else a
        for a in group.assignments
    )
    return group.model_copy(update={"assignments": new_assignments})
