"""Solo-execution agent selection for the work pipeline.

Extracted from :mod:`synthorg.engine.pipeline.service` so the pipeline
service stays within its module-size budget. The scoring + assignment-
service routing is one cohesive concern: rank the viable agents for a
leaf task and return the winner (or raise when none is eligible).
"""

from synthorg.core.agent import AgentIdentity
from synthorg.core.task import Task
from synthorg.engine.assignment.models import AssignmentRequest
from synthorg.engine.assignment.service import TaskAssignmentService
from synthorg.engine.decomposition.models import SubtaskDefinition
from synthorg.engine.pipeline.errors import WorkRoutingUndecidableError
from synthorg.engine.routing.scorer import AgentTaskScorer
from synthorg.observability import get_logger
from synthorg.observability.events.pipeline import (
    PIPELINE_ROUTING_UNDECIDABLE,
    PIPELINE_SOLO_AGENT_SELECTED,
)

logger = get_logger(__name__)


def select_solo_agent(
    task: Task,
    agents: tuple[AgentIdentity, ...],
    *,
    scorer: AgentTaskScorer,
    assignment_service: TaskAssignmentService | None,
) -> str:
    """Pick the top-scoring viable agent for the leaf task.

    Routes through ``assignment_service`` when present (so its task-status
    validation and project-team filter run) and otherwise scores the
    candidates directly.

    Returns:
        The ID of the highest-scoring viable agent (tie-broken by stable
        lexicographic id).

    Raises:
        WorkRoutingUndecidableError: When ``agents`` is empty or no agent
            scored above :attr:`AgentTaskScorer.min_score`.
    """
    if not agents:
        msg = "no active agents available for solo execution"
        logger.warning(
            PIPELINE_ROUTING_UNDECIDABLE,
            task_id=str(task.id),
            reason="no_active_agents",
            path="solo",
            error_type=WorkRoutingUndecidableError.__name__,
        )
        raise WorkRoutingUndecidableError(msg)
    if assignment_service is not None:
        return _select_via_service(
            task, agents, scorer=scorer, service=assignment_service
        )
    proxy = SubtaskDefinition(
        id=str(task.id),
        title=task.title,
        description=task.description,
        estimated_complexity=task.estimated_complexity,
    )
    candidates = [scorer.score(agent, proxy) for agent in agents]
    viable = [c for c in candidates if c.score >= scorer.min_score]
    if not viable:
        msg = (
            "no agent scored above the routing threshold "
            f"({scorer.min_score}) for solo execution"
        )
        logger.warning(
            PIPELINE_ROUTING_UNDECIDABLE,
            task_id=str(task.id),
            reason="no_agent_above_threshold",
            min_score=scorer.min_score,
            candidate_count=len(candidates),
            error_type=WorkRoutingUndecidableError.__name__,
        )
        raise WorkRoutingUndecidableError(msg)
    best = max(viable, key=lambda c: (c.score, str(c.agent_identity.id)))
    assigned_id = str(best.agent_identity.id)
    logger.info(
        PIPELINE_SOLO_AGENT_SELECTED,
        task_id=str(task.id),
        agent_id=assigned_id,
        score=best.score,
    )
    return assigned_id


def _select_via_service(
    task: Task,
    agents: tuple[AgentIdentity, ...],
    *,
    scorer: AgentTaskScorer,
    service: TaskAssignmentService,
) -> str:
    """Select the solo agent through the assignment service layer.

    Routes the pick through ``TaskAssignmentService`` so its task-status
    validation (rejecting non-assignable statuses) and project-team filter
    run before the same scorer-backed strategy ranks candidates.

    Returns:
        The ID of the selected agent.

    Raises:
        TaskAssignmentError: Propagated when the task status is not
            eligible for assignment.
        WorkRoutingUndecidableError: When the service selects no eligible
            agent (none scored above the threshold or none survived the
            project-team filter).
    """
    request = AssignmentRequest(
        task=task,
        available_agents=agents,
        min_score=scorer.min_score,
    )
    result = service.assign(request)
    if result.selected is None:
        msg = (
            f"assignment service selected no agent for solo execution: {result.reason}"
        )
        logger.warning(
            PIPELINE_ROUTING_UNDECIDABLE,
            task_id=str(task.id),
            reason="assignment_service_no_selection",
            path="solo",
            strategy=result.strategy_used,
            assignment_reason=result.reason,
            error_type=WorkRoutingUndecidableError.__name__,
        )
        raise WorkRoutingUndecidableError(msg)
    assigned_id = str(result.selected.agent_identity.id)
    logger.info(
        PIPELINE_SOLO_AGENT_SELECTED,
        task_id=str(task.id),
        agent_id=assigned_id,
        score=result.selected.score,
    )
    return assigned_id
