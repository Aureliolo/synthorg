"""Agent-task scoring for routing decisions.

Scores how well an agent matches a subtask based on skill overlap,
role match, and seniority-complexity alignment.
"""

from typing import TYPE_CHECKING

from synthorg.core.enums import AgentStatus, Complexity, SeniorityLevel
from synthorg.engine.routing.models import RoutingCandidate
from synthorg.observability import get_logger
from synthorg.observability.events.task_routing import (
    TASK_ROUTING_AGENT_SCORED,
    TASK_ROUTING_SCORER_INVALID_CONFIG,
)

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity
    from synthorg.engine.decomposition.models import SubtaskDefinition

logger = get_logger(__name__)

# Score-component weights. Tuned empirically; sum to 1.1 with the tag
# bonus, capped at 1.0 by the caller. Extracted to module-level
# constants so the same value drives both the docstring and the
# arithmetic in one place.
_PRIMARY_SKILL_WEIGHT = 0.4
_SECONDARY_SKILL_WEIGHT = 0.2
_TAG_MATCH_BONUS = 0.1
_ROLE_MATCH_BONUS = 0.2
_SENIORITY_ALIGNMENT_BONUS = 0.2

# Seniority-to-complexity alignment mapping
_SENIORITY_COMPLEXITY: dict[SeniorityLevel, tuple[Complexity, ...]] = {
    SeniorityLevel.JUNIOR: (Complexity.SIMPLE,),
    SeniorityLevel.MID: (Complexity.SIMPLE, Complexity.MEDIUM),
    SeniorityLevel.SENIOR: (Complexity.MEDIUM, Complexity.COMPLEX),
    SeniorityLevel.LEAD: (Complexity.COMPLEX, Complexity.EPIC),
    SeniorityLevel.PRINCIPAL: (Complexity.COMPLEX, Complexity.EPIC),
    SeniorityLevel.DIRECTOR: (Complexity.EPIC,),
    SeniorityLevel.VP: (Complexity.EPIC,),
    SeniorityLevel.C_SUITE: (Complexity.EPIC,),
}


class AgentTaskScorer:
    """Scores agent-subtask compatibility for routing.

    Scoring heuristics (skill tiers are proficiency-weighted: the
    per-skill contribution equals the agent's proficiency for that
    skill; default proficiency ``1.0`` reproduces legacy boolean-match
    behaviour):

    - Primary skill overlap: sum(proficiency for matched primary)
      / max(required, 1) * 0.4
    - Secondary skill overlap: sum(proficiency for matched secondary)
      / max(required, 1) * 0.2 (skills already matched by primary are
      excluded)
    - Tag match (when ``required_tags`` is set and every required tag
      is covered by the union of tags on matched skills): +0.1
    - Role match (if required_role set): +0.2
    - Seniority-complexity alignment: +0.2
    - Score capped at 1.0
    - Agent must be ACTIVE status

    When the subtask has no ``required_skills``, skill-overlap and
    tag-match components (0.7 total weight) are skipped, and the
    maximum score is 0.4 (role 0.2 + seniority 0.2). If
    ``required_role`` is also not set, the maximum score is 0.2
    (seniority only).
    """

    __slots__ = ("_min_score",)

    def __init__(self, min_score: float = 0.1) -> None:
        if not 0.0 <= min_score <= 1.0:
            msg = f"min_score must be between 0.0 and 1.0, got {min_score}"
            logger.warning(
                TASK_ROUTING_SCORER_INVALID_CONFIG,
                min_score=min_score,
                error=msg,
            )
            raise ValueError(msg)
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

        reasons: list[str] = []
        total_score, all_matched = _score_skill_tiers(agent, subtask, reasons)
        total_score += _score_role(agent, subtask, reasons)
        total_score += _score_seniority_alignment(agent, subtask, reasons)

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


def _score_skill_tiers(
    agent: AgentIdentity,
    subtask: SubtaskDefinition,
    reasons: list[str],
) -> tuple[float, list[str]]:
    """Score primary, secondary, and tag tiers; return (score, matched_ids).

    Mutates *reasons* with human-readable explanations.
    """
    required = set(subtask.required_skills)
    if not required:
        reasons.append("no skills required, skill matching skipped")
        return 0.0, []

    primary_by_id = {s.id: s for s in agent.skills.primary}
    secondary_by_id = {s.id: s for s in agent.skills.secondary}
    # Sort matched ids so proficiency sums are deterministic regardless of
    # set iteration order (hash randomization).
    primary_matched = sorted(required & primary_by_id.keys())
    secondary_matched = sorted(
        (required & secondary_by_id.keys()) - set(primary_matched),
    )

    score = 0.0
    all_matched: list[str] = []

    primary_contrib = (
        sum(primary_by_id[sid].proficiency for sid in primary_matched)
        / len(required)
        * _PRIMARY_SKILL_WEIGHT
    )
    score += primary_contrib
    all_matched.extend(primary_matched)
    if primary_matched:
        reasons.append(f"primary skills: {primary_matched}")

    secondary_contrib = (
        sum(secondary_by_id[sid].proficiency for sid in secondary_matched)
        / len(required)
        * _SECONDARY_SKILL_WEIGHT
    )
    score += secondary_contrib
    all_matched.extend(secondary_matched)
    if secondary_matched:
        reasons.append(f"secondary skills: {secondary_matched}")

    required_tags = set(subtask.required_tags)
    if required_tags:
        matched_tags: set[str] = set()
        for sid in primary_matched:
            matched_tags.update(primary_by_id[sid].tags)
        for sid in secondary_matched:
            matched_tags.update(secondary_by_id[sid].tags)
        if required_tags <= matched_tags:
            score += _TAG_MATCH_BONUS
            reasons.append(f"tag match: {sorted(required_tags)}")

    return score, all_matched


def _score_role(
    agent: AgentIdentity,
    subtask: SubtaskDefinition,
    reasons: list[str],
) -> float:
    """Award the role-match bonus when the agent's role matches required_role."""
    if (
        subtask.required_role is not None
        and agent.role.casefold() == subtask.required_role.casefold()
    ):
        reasons.append("role match")
        return _ROLE_MATCH_BONUS
    return 0.0


def _score_seniority_alignment(
    agent: AgentIdentity,
    subtask: SubtaskDefinition,
    reasons: list[str],
) -> float:
    """Award the seniority-alignment bonus when level matches complexity."""
    aligned = _SENIORITY_COMPLEXITY.get(agent.level, ())
    if subtask.estimated_complexity in aligned:
        reasons.append(
            f"seniority {agent.level.value} aligns with "
            f"complexity {subtask.estimated_complexity.value}"
        )
        return _SENIORITY_ALIGNMENT_BONUS
    return 0.0
