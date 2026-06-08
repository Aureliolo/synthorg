"""Shared helpers and constants for task assignment strategies.

``STRATEGY_NAME_*`` constants identify each strategy.
``build_subtask_definition`` and ``score_and_filter_candidates``
are shared by all scorer-based strategies.
"""

from typing import TYPE_CHECKING, Final

from synthorg.engine.assignment.models import (
    AssignmentCandidate,
    AssignmentRequest,
)
from synthorg.engine.decomposition.models import SubtaskDefinition
from synthorg.hr.enums import AgentStatus
from synthorg.observability import get_logger
from synthorg.observability.events.task_assignment import (
    TASK_ASSIGNMENT_AGENT_SCORED,
    TASK_ASSIGNMENT_WORKLOAD_MISSING,
)

if TYPE_CHECKING:
    from synthorg.engine.routing.scorer import AgentTaskScorer

logger = get_logger(__name__)

STRATEGY_NAME_MANUAL: Final[str] = "manual"
STRATEGY_NAME_ROLE_BASED: Final[str] = "role_based"
STRATEGY_NAME_LOAD_BALANCED: Final[str] = "load_balanced"
STRATEGY_NAME_COST_OPTIMIZED: Final[str] = "cost_optimized"
STRATEGY_NAME_HIERARCHICAL: Final[str] = "hierarchical"
STRATEGY_NAME_AUCTION: Final[str] = "auction"


def build_subtask_definition(request: AssignmentRequest) -> SubtaskDefinition:
    """Build a SubtaskDefinition adapter from an AssignmentRequest.

    Maps task-level fields (id, title, description, estimated_complexity)
    from the request's task and scoring hints (required_skills,
    required_role) from the request itself into a ``SubtaskDefinition``.

    Args:
        request: The assignment request.

    Returns:
        A SubtaskDefinition for scoring purposes.
    """
    return SubtaskDefinition(
        id=str(request.task.id),
        title=request.task.title,
        description=request.task.description,
        estimated_complexity=request.task.estimated_complexity,
        required_skills=request.required_skills,
        required_role=request.required_role,
    )


def score_and_filter_candidates(
    scorer: AgentTaskScorer,
    request: AssignmentRequest,
    subtask: SubtaskDefinition,
) -> list[AssignmentCandidate]:
    """Score all agents and return filtered, sorted candidates.

    Shared scoring logic used by all scorer-based strategies.
    Filters out agents with non-ACTIVE status and agents at
    capacity (when ``max_concurrent_tasks`` and workload data
    are available) before scoring. Agents not present in the
    workload data are assumed to have zero active tasks and
    will not be filtered for capacity.

    Args:
        scorer: The agent-task scorer to use.
        request: The assignment request.
        subtask: The subtask definition for scoring.

    Returns:
        Sorted list of candidates whose score meets or exceeds
        ``request.min_score``, ordered by score descending.
    """
    workload_map: dict[str, int] | None = None
    if request.max_concurrent_tasks is not None and request.workloads:
        workload_map = {w.agent_id: w.active_task_count for w in request.workloads}

    candidates: list[AssignmentCandidate] = []
    for agent in request.available_agents:
        if agent.status != AgentStatus.ACTIVE:
            continue

        if workload_map is not None and request.max_concurrent_tasks is not None:
            agent_id_str = str(agent.id)
            if agent_id_str not in workload_map:
                logger.debug(
                    TASK_ASSIGNMENT_WORKLOAD_MISSING,
                    task_id=str(request.task.id),
                    agent_name=agent.name,
                )
            active = workload_map.get(agent_id_str, 0)
            if active >= request.max_concurrent_tasks:
                logger.debug(
                    TASK_ASSIGNMENT_AGENT_SCORED,
                    task_id=str(request.task.id),
                    agent_name=agent.name,
                    score=0.0,
                    reason="at_capacity",
                    active_tasks=active,
                    max_concurrent=request.max_concurrent_tasks,
                )
                continue

        routing_candidate = scorer.score(agent, subtask)

        logger.debug(
            TASK_ASSIGNMENT_AGENT_SCORED,
            task_id=str(request.task.id),
            agent_name=agent.name,
            score=routing_candidate.score,
        )

        if routing_candidate.score >= request.min_score:
            candidates.append(
                AssignmentCandidate(
                    agent_identity=routing_candidate.agent_identity,
                    score=routing_candidate.score,
                    matched_skills=routing_candidate.matched_skills,
                    reason=routing_candidate.reason,
                ),
            )

    return sorted(candidates, key=lambda c: c.score, reverse=True)
