"""Agent-task scoring for routing decisions.

Scores how well an agent matches a subtask based on skill overlap,
role match, and seniority-complexity alignment. Operator-tunable
weights and the minimum candidate score live in :mod:`settings`
under ``engine.routing.*`` and reach the scorer via
:class:`RoutingScorerConfig` (resolved at construction time).
"""

from typing import TYPE_CHECKING, Final

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.enums import AgentStatus
from synthorg.core.normalization import compare_ci
from synthorg.core.task_enums import Complexity
from synthorg.engine.routing.models import RoutingCandidate
from synthorg.hr.seniority import SeniorityLevel
from synthorg.observability import get_logger
from synthorg.observability.events.task_routing import (
    TASK_ROUTING_AGENT_SCORED,
    TASK_ROUTING_SCORER_INVALID_CONFIG,
)

if TYPE_CHECKING:
    from synthorg.core.agent import AgentIdentity
    from synthorg.engine.decomposition.models import SubtaskDefinition
    from synthorg.settings.bridge_configs import EngineBridgeConfig

logger = get_logger(__name__)

# Soft validator ceilings on the documented routing-weight envelope.
# The per-field bounds (Field ge/le) prevent invalid individual
# weights; these constants only drive the aggregate sanity warning
# emitted by ``_check_weight_sum``. Hardcoded here rather than read
# from settings because the validator runs at Pydantic construction
# time -- before any resolver is available -- and the values describe
# a documented design envelope, not an operator-tunable knob.
_DOC_WEIGHT_SUM_MAX: Final[float] = 1.1
_WEIGHT_SUM_WARN_CEILING: Final[float] = 1.3


class RoutingScorerConfig(BaseModel):
    """Operator-tunable configuration for :class:`AgentTaskScorer`.

    Field defaults mirror the historical hardcoded values so a default
    construction reproduces legacy behaviour. Production wiring
    populates the fields from :func:`ConfigResolver.get_engine_bridge_config`
    via :meth:`from_bridge_config` so operators can tune via ``/settings``
    without code changes. Sum of skill-weights + bonuses is 1.1 (tag
    bonus pushes the maximum above 1.0); the caller caps the final
    score at 1.0.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    primary_skill_weight: float = Field(default=0.4, ge=0.0, le=1.0)
    secondary_skill_weight: float = Field(default=0.2, ge=0.0, le=1.0)
    tag_match_bonus: float = Field(default=0.1, ge=0.0, le=1.0)
    role_match_bonus: float = Field(default=0.2, ge=0.0, le=1.0)
    seniority_alignment_bonus: float = Field(default=0.2, ge=0.0, le=1.0)
    min_score: float = Field(default=0.1, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _check_weight_sum(self) -> RoutingScorerConfig:
        """Warn (do not reject) when weights leave the documented envelope.

        The scorer caps the final score at 1.0, so weight sums above
        the documented ceiling produce surprising-but-bounded
        behaviour. A logged warning surfaces operator-tunable
        misconfiguration without breaking the resolver hot path
        (Pydantic ``ValidationError`` would block bridge resolution
        and crash bootstrap).

        Returns:
            ``self`` unchanged; weight sums above the documented
            ceiling only log a warning.
        """
        weight_sum = (
            self.primary_skill_weight
            + self.secondary_skill_weight
            + self.tag_match_bonus
            + self.role_match_bonus
            + self.seniority_alignment_bonus
        )
        if weight_sum > _DOC_WEIGHT_SUM_MAX:
            logger.warning(
                TASK_ROUTING_SCORER_INVALID_CONFIG,
                weight_sum=weight_sum,
                error=(
                    f"routing weights sum to {weight_sum:.3f} "
                    f"(documented max ~{_DOC_WEIGHT_SUM_MAX}, hard ceiling "
                    f"{_WEIGHT_SUM_WARN_CEILING}); final score is still capped at 1.0"
                ),
            )
        return self

    @classmethod
    def from_bridge_config(cls, bridge: EngineBridgeConfig) -> RoutingScorerConfig:
        """Project the routing-scorer subset out of an ``EngineBridgeConfig``.

        ``EngineBridgeConfig`` is imported under ``TYPE_CHECKING`` so the
        routing module stays free of a runtime dependency on
        ``settings.bridge_configs`` (which would cycle through the engine
        namespace).

        Returns:
            A :class:`RoutingScorerConfig` populated from the
            ``routing_*`` fields of ``bridge``.
        """
        return cls(
            primary_skill_weight=bridge.routing_weight_primary_skill,
            secondary_skill_weight=bridge.routing_weight_secondary_skill,
            tag_match_bonus=bridge.routing_weight_tag_match_bonus,
            role_match_bonus=bridge.routing_weight_role_match_bonus,
            seniority_alignment_bonus=bridge.routing_weight_seniority_alignment_bonus,
            min_score=bridge.routing_min_score,
        )


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
    behaviour). All weights and the minimum-candidate-score threshold
    are read from ``config`` so operators tune via ``/settings``:

    - Primary skill overlap: sum(proficiency for matched primary)
      / max(required, 1) * ``config.primary_skill_weight``
    - Secondary skill overlap: sum(proficiency for matched secondary)
      / max(required, 1) * ``config.secondary_skill_weight``
      (skills already matched by primary are excluded)
    - Tag match (when ``required_tags`` is set and every required tag
      is covered by the union of tags on matched skills):
      + ``config.tag_match_bonus``
    - Role match (if required_role set): + ``config.role_match_bonus``
    - Seniority-complexity alignment:
      + ``config.seniority_alignment_bonus``
    - Score capped at 1.0
    - Agent must be ACTIVE status

    When the subtask has no ``required_skills``, skill-overlap and
    tag-match components are skipped; the remaining score ceiling is
    ``role_match_bonus + seniority_alignment_bonus``. If
    ``required_role`` is also not set, the ceiling collapses to
    ``seniority_alignment_bonus``.
    """

    __slots__ = ("_config", "_min_score")

    def __init__(
        self,
        *,
        min_score: float | None = None,
        config: RoutingScorerConfig | None = None,
    ) -> None:
        self._config = config if config is not None else RoutingScorerConfig()
        effective_min = min_score if min_score is not None else self._config.min_score
        if not 0.0 <= effective_min <= 1.0:
            msg = f"min_score must be between 0.0 and 1.0, got {effective_min}"
            logger.warning(
                TASK_ROUTING_SCORER_INVALID_CONFIG,
                min_score=effective_min,
                error=msg,
            )
            raise ValueError(msg)
        self._min_score = effective_min

    @property
    def min_score(self) -> float:
        """Minimum score threshold for a viable candidate."""
        return self._min_score

    @property
    def config(self) -> RoutingScorerConfig:
        """Snapshot of the operator-tunable scorer config."""
        return self._config

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
        total_score, all_matched = _score_skill_tiers(
            agent, subtask, reasons, self._config
        )
        total_score += _score_role(agent, subtask, reasons, self._config)
        total_score += _score_seniority_alignment(agent, subtask, reasons, self._config)

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
    config: RoutingScorerConfig,
) -> tuple[float, list[str]]:
    """Score primary, secondary, and tag tiers; return (score, matched_ids).

    Mutates *reasons* with human-readable explanations.

    Returns:
        ``(score, matched_ids)`` -- the aggregated skill / tag score
        and the sorted list of matched skill ids (primary first,
        then secondary).
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
        * config.primary_skill_weight
    )
    score += primary_contrib
    all_matched.extend(primary_matched)
    if primary_matched:
        reasons.append(f"primary skills: {primary_matched}")

    secondary_contrib = (
        sum(secondary_by_id[sid].proficiency for sid in secondary_matched)
        / len(required)
        * config.secondary_skill_weight
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
            score += config.tag_match_bonus
            reasons.append(f"tag match: {sorted(required_tags)}")

    return score, all_matched


def _score_role(
    agent: AgentIdentity,
    subtask: SubtaskDefinition,
    reasons: list[str],
    config: RoutingScorerConfig,
) -> float:
    """Award the role-match bonus when the agent's role matches required_role.

    Returns:
        ``config.role_match_bonus`` when the agent's role matches the
        subtask's required role (case-insensitive); ``0.0`` otherwise.
    """
    if subtask.required_role is not None and compare_ci(
        agent.role, subtask.required_role
    ):
        reasons.append("role match")
        return config.role_match_bonus
    return 0.0


def _score_seniority_alignment(
    agent: AgentIdentity,
    subtask: SubtaskDefinition,
    reasons: list[str],
    config: RoutingScorerConfig,
) -> float:
    """Award the seniority-alignment bonus when level matches complexity.

    Returns:
        ``config.seniority_alignment_bonus`` when the agent's
        seniority covers the subtask's estimated complexity; ``0.0``
        otherwise.
    """
    aligned = _SENIORITY_COMPLEXITY.get(agent.level, ())
    if subtask.estimated_complexity in aligned:
        reasons.append(
            f"seniority {agent.level.value} aligns with "
            f"complexity {subtask.estimated_complexity.value}"
        )
        return config.seniority_alignment_bonus
    return 0.0
