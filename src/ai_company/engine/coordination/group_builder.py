"""DAG-to-execution-wave converter.

Translates decomposition results and routing decisions into
``ParallelExecutionGroup`` instances ready for the executor.
"""

from typing import TYPE_CHECKING

from ai_company.engine.decomposition.dag import DependencyGraph
from ai_company.engine.errors import CoordinationError
from ai_company.engine.parallel_models import (
    AgentAssignment,
    ParallelExecutionGroup,
)
from ai_company.observability import get_logger
from ai_company.observability.events.coordination import (
    COORDINATION_WAVE_BUILT,
)

if TYPE_CHECKING:
    from ai_company.engine.coordination.config import CoordinationConfig
    from ai_company.engine.decomposition.models import DecompositionResult
    from ai_company.engine.routing.models import RoutingDecision, RoutingResult
    from ai_company.engine.workspace.models import Workspace

logger = get_logger(__name__)


def _build_workspace_lookup(
    workspaces: tuple[Workspace, ...],
) -> dict[str, str]:
    """Map task_id → worktree_path from workspaces."""
    return {ws.task_id: ws.worktree_path for ws in workspaces}


def _build_routing_lookup(
    routing_result: RoutingResult,
) -> dict[str, RoutingDecision]:
    """Map subtask_id → RoutingDecision."""
    return {d.subtask_id: d for d in routing_result.decisions}


def build_execution_waves(
    *,
    decomposition_result: DecompositionResult,
    routing_result: RoutingResult,
    config: CoordinationConfig,
    workspaces: tuple[Workspace, ...] = (),
) -> tuple[ParallelExecutionGroup, ...]:
    """Convert DAG parallel groups + routing decisions into execution groups.

    1. Reconstruct ``DependencyGraph`` from plan subtasks.
    2. Call ``dag.parallel_groups()`` to get wave-level grouping.
    3. For each wave, look up ``RoutingDecision`` to build
       ``AgentAssignment``.
    4. Map workspace ``worktree_path`` to ``resource_claims``.
    5. Return tuple of ``ParallelExecutionGroup`` (one per wave).

    Subtasks without a routing decision are skipped with a debug log
    (they were already reported as unroutable by the routing phase).

    Args:
        decomposition_result: Decomposition with plan and created tasks.
        routing_result: Routing decisions mapping subtasks to agents.
        config: Coordination configuration.
        workspaces: Optional workspaces for resource claim mapping.

    Returns:
        Tuple of execution groups, one per wave.
    """
    plan = decomposition_result.plan
    dag = DependencyGraph(plan.subtasks)

    parallel_groups = dag.parallel_groups()
    routing_lookup = _build_routing_lookup(routing_result)
    workspace_lookup = _build_workspace_lookup(workspaces)
    task_lookup = {t.id: t for t in decomposition_result.created_tasks}
    dep_map = {s.id: s.dependencies for s in plan.subtasks}

    groups: list[ParallelExecutionGroup] = []
    blocked_ids: set[str] = set()

    for wave_idx, subtask_ids in enumerate(parallel_groups):
        assignments: list[AgentAssignment] = []

        for subtask_id in subtask_ids:
            # Block subtasks whose prerequisites are blocked
            deps = dep_map.get(subtask_id, ())
            blocked_deps = set(deps) & blocked_ids
            if blocked_deps:
                blocked_ids.add(subtask_id)
                logger.debug(
                    COORDINATION_WAVE_BUILT,
                    wave_index=wave_idx,
                    subtask_id=subtask_id,
                    skipped=True,
                    reason="blocked by unroutable prerequisite",
                    blocked_dependencies=sorted(blocked_deps),
                )
                continue

            decision = routing_lookup.get(subtask_id)
            if decision is None:
                blocked_ids.add(subtask_id)
                logger.debug(
                    COORDINATION_WAVE_BUILT,
                    wave_index=wave_idx,
                    subtask_id=subtask_id,
                    skipped=True,
                    reason="no routing decision",
                )
                continue

            task = task_lookup.get(subtask_id)
            if task is None:
                msg = (
                    f"Subtask {subtask_id!r} has a routing decision "
                    "but no corresponding created task in decomposition"
                )
                logger.warning(
                    COORDINATION_WAVE_BUILT,
                    wave_index=wave_idx,
                    subtask_id=subtask_id,
                    error=msg,
                )
                raise CoordinationError(msg)
            candidate = decision.selected_candidate

            resource_claims: tuple[str, ...] = ()
            worktree_path = workspace_lookup.get(subtask_id)
            if worktree_path:
                resource_claims = (worktree_path,)

            assignments.append(
                AgentAssignment(
                    identity=candidate.agent_identity,
                    task=task,
                    resource_claims=resource_claims,
                )
            )

        if not assignments:
            logger.debug(
                COORDINATION_WAVE_BUILT,
                wave_index=wave_idx,
                assignment_count=0,
                skipped=True,
                reason="all subtasks unroutable in wave",
            )
            continue

        group_id = f"wave-{wave_idx}"
        logger.debug(
            COORDINATION_WAVE_BUILT,
            wave_index=wave_idx,
            assignment_count=len(assignments),
            group_id=group_id,
        )

        groups.append(
            ParallelExecutionGroup(
                group_id=group_id,
                assignments=tuple(assignments),
                max_concurrency=config.max_concurrency_per_wave,
                fail_fast=config.fail_fast,
            )
        )

    return tuple(groups)
