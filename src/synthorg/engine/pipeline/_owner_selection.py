# module-kind: code
"""Project-owner selection for the work pipeline.

Staffs a single accountable owner for a planned initiative from the standing
roster, so a greenlit objective is owned rather than run as an anonymous solo
task. Unlike solo-agent selection (:mod:`_solo_selection`) this never fails the
run on scoring: when no agent clears the routing threshold it falls back to the
most senior available agent, so a staffed initiative is owned whenever the
roster is non-empty.
"""

from enum import StrEnum
from functools import cmp_to_key

from synthorg.core.agent import AgentIdentity
from synthorg.core.authority import compare_authority
from synthorg.core.task import Task
from synthorg.engine.decomposition.models import SubtaskDefinition
from synthorg.engine.routing.scorer import AgentTaskScorer
from synthorg.observability import get_logger
from synthorg.observability.events.pipeline import PIPELINE_PROJECT_OWNER_SELECTED

logger = get_logger(__name__)


class OwnerSelectionMethod(StrEnum):
    """How :func:`select_project_owner` arrived at the owner."""

    SCORED = "scored"
    SENIORITY_FALLBACK = "seniority_fallback"


def select_project_owner(
    task: Task,
    agents: tuple[AgentIdentity, ...],
    *,
    scorer: AgentTaskScorer,
) -> AgentIdentity | None:
    """Pick the accountable owner for a planned initiative.

    Scores every candidate against the objective (via the same
    :class:`AgentTaskScorer` the router uses) and returns the top-scoring
    one. When none clears the routing threshold, the most senior agent is
    chosen so a non-empty roster still yields an owner. Ties break on a stable
    lexicographic id so the pick is deterministic.

    Args:
        task: The objective task the initiative delivers.
        agents: The active roster to staff the owner from.
        scorer: Shared agent-task scorer (also used by the router).

    Returns:
        The owning :class:`AgentIdentity`, or ``None`` when ``agents`` is
        empty (an unstaffed roster leaves the initiative unowned).
    """
    if not agents:
        return None
    proxy = SubtaskDefinition(
        id=str(task.id),
        title=task.title,
        description=task.description,
        estimated_complexity=task.estimated_complexity,
    )
    viable = [
        candidate
        for candidate in (scorer.score(agent, proxy) for agent in agents)
        if candidate.score >= scorer.min_score
    ]
    if viable:
        best = max(viable, key=lambda c: (c.score, str(c.agent_identity.id)))
        owner = best.agent_identity
        selection, score = OwnerSelectionMethod.SCORED, best.score
    else:
        authority_key = cmp_to_key(compare_authority)
        owner = max(agents, key=lambda a: (authority_key(a.role), str(a.id)))
        selection, score = OwnerSelectionMethod.SENIORITY_FALLBACK, 0.0
    logger.info(
        PIPELINE_PROJECT_OWNER_SELECTED,
        task_id=str(task.id),
        owner_id=str(owner.id),
        selection=selection.value,
        score=score,
    )
    return owner
