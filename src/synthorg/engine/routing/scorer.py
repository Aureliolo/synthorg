"""Agent-task scoring for routing decisions.

Scores how well an agent matches a subtask based on skill overlap and
role match. Operator-tunable weights and the minimum candidate score
live in :mod:`settings` under ``engine.routing.*`` and reach the
scorer via :class:`RoutingScorerConfig` (resolved at construction
time).
"""

from typing import Final

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.agent import AgentIdentity
from synthorg.core.normalization import compare_ci
from synthorg.engine.decomposition.models import SubtaskDefinition
from synthorg.engine.routing.models import RoutingCandidate
from synthorg.hr.enums import AgentStatus
from synthorg.observability import get_logger
from synthorg.observability.events.task_routing import (
    TASK_ROUTING_AGENT_INACTIVE_SKIPPED,
    TASK_ROUTING_AGENT_SCORED,
    TASK_ROUTING_SCORER_INVALID_CONFIG,
)
from synthorg.settings.bridge_configs import EngineBridgeConfig

logger = get_logger(__name__)

# Soft validator ceilings on the documented routing-weight envelope.
# The per-field bounds (Field ge/le) prevent invalid individual
# weights; these constants only drive the aggregate sanity warning
# emitted by ``_check_weight_sum``. Hardcoded here rather than read
# from settings because the validator runs at Pydantic construction
# time -- before any resolver is available -- and the values describe
# a documented design envelope, not an operator-tunable knob.
_DOC_WEIGHT_SUM_MAX: Final[float] = 0.9
_WEIGHT_SUM_WARN_CEILING: Final[float] = 1.1
# The field defaults sum to 0.9000000000000001 in binary floating point, so an
# exact ``>`` comparison makes the stock configuration warn about itself. The
# tolerance is far below any weight an operator can express.
_WEIGHT_SUM_TOLERANCE: Final[float] = 1e-9


class RoutingScorerConfig(BaseModel):
    """Operator-tunable configuration for :class:`AgentTaskScorer`.

    Field defaults mirror the historical hardcoded values so a default
    construction reproduces legacy behaviour. Production wiring
    populates the fields from :func:`ConfigResolver.get_engine_bridge_config`
    via :meth:`from_bridge_config` so operators can tune via ``/settings``
    without code changes. Sum of skill-weights + bonuses is 0.9 by
    default; the caller caps the final score at 1.0, which matters only
    when an operator tunes weights above the documented envelope.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    primary_skill_weight: float = Field(default=0.4, ge=0.0, le=1.0)
    secondary_skill_weight: float = Field(default=0.2, ge=0.0, le=1.0)
    tag_match_bonus: float = Field(default=0.1, ge=0.0, le=1.0)
    role_match_bonus: float = Field(default=0.2, ge=0.0, le=1.0)
    min_score: float = Field(default=0.1, ge=0.0, le=1.0)
    low_confidence_score: float = Field(default=0.35, ge=0.0, le=1.0)

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
        )
        if weight_sum > _DOC_WEIGHT_SUM_MAX + _WEIGHT_SUM_TOLERANCE:
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

        Returns:
            A :class:`RoutingScorerConfig` populated from the
            ``routing_*`` fields of ``bridge``.
        """
        return cls(
            primary_skill_weight=bridge.routing_weight_primary_skill,
            secondary_skill_weight=bridge.routing_weight_secondary_skill,
            tag_match_bonus=bridge.routing_weight_tag_match_bonus,
            role_match_bonus=bridge.routing_weight_role_match_bonus,
            min_score=bridge.routing_min_score,
            low_confidence_score=bridge.routing_low_confidence_score,
        )


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
    - Score capped at 1.0
    - Agent must be ACTIVE status

    When the subtask has no ``required_skills``, skill-overlap and
    tag-match components are skipped; the remaining score ceiling is
    ``role_match_bonus``. If ``required_role`` is also not set, the
    ceiling collapses to zero.
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
    def low_confidence_score(self) -> float:
        """Score below which a winning fit is low-confidence.

        Clamped to at least the effective ``min_score`` so a caller that raised
        the eligibility floor above the configured band never leaves the band
        below the floor.
        """
        return max(self._config.low_confidence_score, self._min_score)

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
            return _inactive_candidate(agent, subtask)

        skill_score, all_matched, skill_reasons = _score_skill_tiers(
            agent, subtask, self._config
        )
        role_score, role_reason = _score_role(agent, subtask, self._config)

        total_score = min(skill_score + role_score, 1.0)
        reasons = [*skill_reasons, role_reason] if role_reason else list(skill_reasons)
        reason = "; ".join(reasons) if reasons else "no matching criteria"

        logger.debug(
            TASK_ROUTING_AGENT_SCORED,
            agent_name=agent.name,
            subtask_id=subtask.id,
            score=total_score,
            reason=reason,
        )

        return RoutingCandidate(
            agent_identity=agent,
            score=total_score,
            matched_skills=tuple(all_matched),
            reason=reason,
        )


def _inactive_candidate(
    agent: AgentIdentity,
    subtask: SubtaskDefinition,
) -> RoutingCandidate:
    """Return the zero-score candidate for a non-active agent.

    Returns:
        A :class:`RoutingCandidate` with score ``0.0`` and a reason
        naming the agent's non-active status.
    """
    logger.debug(
        TASK_ROUTING_AGENT_INACTIVE_SKIPPED,
        agent_name=agent.name,
        subtask_id=subtask.id,
        status=agent.status.value,
    )
    return RoutingCandidate(
        agent_identity=agent,
        score=0.0,
        matched_skills=(),
        reason=f"Agent status is {agent.status.value}, not active",
    )


def _score_skill_tiers(
    agent: AgentIdentity,
    subtask: SubtaskDefinition,
    config: RoutingScorerConfig,
) -> tuple[float, list[str], tuple[str, ...]]:
    """Score primary, secondary, and tag tiers.

    Returns:
        ``(score, matched_ids, reason_fragments)`` -- the aggregated
        skill / tag score, the sorted list of matched skill ids
        (primary first, then secondary), and the human-readable
        explanation fragments for each tier that contributed.
    """
    required = set(subtask.required_skills)
    if not required:
        return 0.0, [], ("no skills required, skill matching skipped",)

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
    reasons: list[str] = []

    score += (
        sum(primary_by_id[sid].proficiency for sid in primary_matched)
        / len(required)
        * config.primary_skill_weight
    )
    all_matched.extend(primary_matched)
    if primary_matched:
        reasons.append(f"primary skills: {primary_matched}")

    score += (
        sum(secondary_by_id[sid].proficiency for sid in secondary_matched)
        / len(required)
        * config.secondary_skill_weight
    )
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

    return score, all_matched, tuple(reasons)


def _score_role(
    agent: AgentIdentity,
    subtask: SubtaskDefinition,
    config: RoutingScorerConfig,
) -> tuple[float, str | None]:
    """Award the role-match bonus when the agent's role matches required_role.

    Returns:
        ``(config.role_match_bonus, "role match")`` when the agent's
        role matches the subtask's required role (case-insensitive);
        ``(0.0, None)`` otherwise.
    """
    if subtask.required_role is not None and compare_ci(
        agent.role, subtask.required_role
    ):
        return config.role_match_bonus, "role match"
    return 0.0, None
