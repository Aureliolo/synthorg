"""Agent-task scoring for routing decisions.

Scores how well an agent matches a subtask based on skill overlap,
role match, and seniority-complexity alignment.
"""

from typing import TYPE_CHECKING

from ai_company.core.enums import AgentStatus, Complexity, SeniorityLevel
from ai_company.engine.routing.models import RoutingCandidate
from ai_company.observability import get_logger
from ai_company.observability.events.task_routing import (
    TASK_ROUTING_AGENT_SCORED,
)

if TYPE_CHECKING:
    from ai_company.core.agent import AgentIdentity
    from ai_company.engine.decomposition.models import SubtaskDefinition

logger = get_logger(__name__)

# Seniority-to-complexity alignment mapping
_SENIORITY_COMPLEXITY: dict[SeniorityLevel, tuple[str, ...]] = {
    SeniorityLevel.JUNIOR: (Complexity.SIMPLE.value,),
    SeniorityLevel.MID: (Complexity.SIMPLE.value, Complexity.MEDIUM.value),
    SeniorityLevel.SENIOR: (Complexity.MEDIUM.value, Complexity.COMPLEX.value),
    SeniorityLevel.LEAD: (Complexity.COMPLEX.value, Complexity.EPIC.value),
    SeniorityLevel.PRINCIPAL: (Complexity.COMPLEX.value, Complexity.EPIC.value),
    SeniorityLevel.DIRECTOR: (Complexity.EPIC.value,),
    SeniorityLevel.VP: (Complexity.EPIC.value,),
    SeniorityLevel.C_SUITE: (Complexity.EPIC.value,),
}


class AgentTaskScorer:
    """Scores agent-subtask compatibility for routing.

    Scoring heuristics:
    - Primary skill overlap: matched/max(required, 1) * 0.4
    - Secondary skill overlap: matched/max(required, 1) * 0.2
    - Role match (if required_role set): +0.2
    - Seniority-complexity alignment: +0.2
    - Score capped at 1.0
    - Agent must be ACTIVE status

    When the subtask has no ``required_skills``, skill-overlap
    components (0.6 total weight) are skipped, and the maximum
    score is 0.4 (role 0.2 + seniority 0.2).
    """

    __slots__ = ("_min_score",)

    def __init__(self, min_score: float = 0.1) -> None:
        self._min_score = min_score

    @property
    def min_score(self) -> float:
        """Minimum score threshold for a viable candidate."""
        return self._min_score

    def score(
        self,
        agent: AgentIdentity,
        subtask: SubtaskDefinition,
    ) -> RoutingCandidate:
        """Score an agent against a subtask definition.

        Args:
            agent: The agent to evaluate.
            subtask: The subtask requirements.

        Returns:
            A routing candidate with the computed score.
        """
        if agent.status != AgentStatus.ACTIVE:
            return RoutingCandidate(
                agent_identity=agent,
                score=0.0,
                matched_skills=(),
                reason=f"Agent status is {agent.status.value}, not active",
            )

        total_score = 0.0
        all_matched: list[str] = []
        reasons: list[str] = []

        # Primary skill overlap (weight: 0.4)
        required = set(subtask.required_skills)
        primary = set(agent.skills.primary)
        primary_matched = required & primary
        if required:
            primary_ratio = len(primary_matched) / max(len(required), 1)
            primary_contrib = primary_ratio * 0.4
            total_score += primary_contrib
            all_matched.extend(sorted(primary_matched))
            if primary_matched:
                reasons.append(f"primary skills: {sorted(primary_matched)}")
        else:
            reasons.append("no skills required, skill matching skipped")

        # Secondary skill overlap (weight: 0.2)
        secondary = set(agent.skills.secondary)
        secondary_matched = required & secondary
        if required:
            secondary_ratio = len(secondary_matched) / max(len(required), 1)
            secondary_contrib = secondary_ratio * 0.2
            total_score += secondary_contrib
            all_matched.extend(sorted(secondary_matched))
            if secondary_matched:
                reasons.append(f"secondary skills: {sorted(secondary_matched)}")

        # Role match (weight: 0.2)
        if (
            subtask.required_role is not None
            and agent.role.casefold() == subtask.required_role.casefold()
        ):
            total_score += 0.2
            reasons.append("role match")

        # Seniority-complexity alignment (weight: 0.2)
        complexity = subtask.estimated_complexity.value
        aligned_complexities = _SENIORITY_COMPLEXITY.get(agent.level, ())
        if complexity in aligned_complexities:
            total_score += 0.2
            reasons.append(
                f"seniority {agent.level.value} aligns with complexity {complexity}"
            )

        # Cap at 1.0
        total_score = min(total_score, 1.0)

        reason = "; ".join(reasons) if reasons else "no matching criteria"

        candidate = RoutingCandidate(
            agent_identity=agent,
            score=total_score,
            matched_skills=tuple(all_matched),
            reason=reason,
        )

        logger.debug(
            TASK_ROUTING_AGENT_SCORED,
            agent_name=agent.name,
            subtask_id=subtask.id,
            score=total_score,
            reason=reason,
        )

        return candidate
