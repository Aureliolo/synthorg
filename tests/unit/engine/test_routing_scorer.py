"""Tests for agent-task scorer."""

from datetime import date

import pytest
from structlog.testing import capture_logs

from synthorg.core.agent import AgentIdentity, ModelConfig, SkillSet
from synthorg.core.role import Skill
from synthorg.core.task_enums import Complexity
from synthorg.engine.decomposition.models import SubtaskDefinition
from synthorg.engine.routing.scorer import AgentTaskScorer, RoutingScorerConfig
from synthorg.hr.enums import AgentStatus
from synthorg.observability.events.task_routing import (
    TASK_ROUTING_AGENT_INACTIVE_SKIPPED,
    TASK_ROUTING_SCORER_INVALID_CONFIG,
)
from tests._shared import as_uuid


def _as_skills(
    ids_or_skills: tuple[str | Skill, ...],
) -> tuple[Skill, ...]:
    """Accept bare IDs or Skill objects and return a tuple of Skills."""
    return tuple(
        s if isinstance(s, Skill) else Skill(id=s, name=s) for s in ids_or_skills
    )


def _make_agent(
    *,
    primary: tuple[str | Skill, ...] = (),
    secondary: tuple[str | Skill, ...] = (),
    role: str = "developer",
    status: AgentStatus = AgentStatus.ACTIVE,
) -> AgentIdentity:
    """Helper to create an agent with specific skills."""
    return AgentIdentity(
        id=as_uuid(f"scorer-agent-{role}"),
        name="Test Agent",
        role=role,
        department="Engineering",
        skills=SkillSet(
            primary=_as_skills(primary),
            secondary=_as_skills(secondary),
        ),
        model=ModelConfig(provider="test-provider", model_id="test-model-001"),
        hiring_date=date(2026, 1, 1),
        status=status,
    )


def _make_subtask(
    *,
    required_skills: tuple[str, ...] = (),
    required_tags: tuple[str, ...] = (),
    required_role: str | None = None,
    complexity: Complexity = Complexity.MEDIUM,
) -> SubtaskDefinition:
    """Helper to create a subtask with requirements."""
    return SubtaskDefinition(
        id="sub-test",
        title="Test Subtask",
        description="A test subtask",
        required_skills=required_skills,
        required_tags=required_tags,
        required_role=required_role,
        estimated_complexity=complexity,
    )


class TestAgentTaskScorer:
    """Tests for AgentTaskScorer."""

    @pytest.mark.unit
    def test_inactive_agent_scores_zero(self) -> None:
        """Inactive agent gets score 0.0."""
        scorer = AgentTaskScorer()
        agent = _make_agent(status=AgentStatus.TERMINATED)
        subtask = _make_subtask(required_skills=("python",))

        candidate = scorer.score(agent, subtask)
        assert candidate.score == 0.0

    @pytest.mark.unit
    def test_inactive_agent_emits_skip_event(self) -> None:
        """Skipping a non-active agent records a decision-point event."""
        scorer = AgentTaskScorer()
        agent = _make_agent(status=AgentStatus.TERMINATED)
        subtask = _make_subtask(required_skills=("python",))

        with capture_logs() as logs:
            scorer.score(agent, subtask)

        skips = [
            entry
            for entry in logs
            if entry.get("event") == TASK_ROUTING_AGENT_INACTIVE_SKIPPED
        ]
        assert len(skips) == 1, f"expected one skip event, got {logs}"
        assert skips[0]["status"] == AgentStatus.TERMINATED.value
        assert skips[0]["agent_name"] == agent.name

    @pytest.mark.unit
    def test_primary_skill_match(self) -> None:
        """Primary skill overlap contributes to score."""
        scorer = AgentTaskScorer()
        agent = _make_agent(primary=("python", "sql"))
        subtask = _make_subtask(required_skills=("python",))

        candidate = scorer.score(agent, subtask)
        assert candidate.score >= 0.4  # Full primary match
        assert "python" in candidate.matched_skills

    @pytest.mark.unit
    def test_secondary_skill_match(self) -> None:
        """Secondary skill overlap contributes to score."""
        scorer = AgentTaskScorer()
        agent = _make_agent(secondary=("python",))
        subtask = _make_subtask(required_skills=("python",))

        candidate = scorer.score(agent, subtask)
        assert candidate.score >= 0.2  # Full secondary match
        assert "python" in candidate.matched_skills

    @pytest.mark.unit
    def test_role_match(self) -> None:
        """Role match adds to score."""
        scorer = AgentTaskScorer()
        agent = _make_agent(role="backend-developer")
        subtask = _make_subtask(required_role="backend-developer")

        candidate = scorer.score(agent, subtask)
        assert candidate.score >= 0.2

    @pytest.mark.unit
    def test_role_match_case_insensitive(self) -> None:
        """Role comparison is case-insensitive."""
        scorer = AgentTaskScorer()
        agent = _make_agent(role="Backend-Developer")
        subtask = _make_subtask(required_role="backend-developer")

        candidate = scorer.score(agent, subtask)
        assert candidate.score >= 0.2

    @pytest.mark.unit
    def test_score_capped_at_one(self) -> None:
        """Weights that would exceed 1.0 are clamped, not merely bounded.

        The stock weights total 0.6 for this shape, so asserting ``<= 1.0``
        against them would pass with the clamp deleted; the cap only earns a
        test against a weight set that would actually overflow it.
        """
        scorer = AgentTaskScorer(
            config=RoutingScorerConfig(
                primary_skill_weight=0.9,
                role_match_bonus=0.9,
            )
        )
        agent = _make_agent(
            primary=("python", "sql"),
            secondary=("testing",),
            role="developer",
        )
        subtask = _make_subtask(
            required_skills=("python",),
            required_role="developer",
            complexity=Complexity.MEDIUM,
        )

        candidate = scorer.score(agent, subtask)
        assert candidate.score == pytest.approx(1.0)

    @pytest.mark.unit
    def test_no_match(self) -> None:
        """No matching criteria gives low score."""
        scorer = AgentTaskScorer()
        agent = _make_agent(primary=("java",), role="frontend")
        subtask = _make_subtask(
            required_skills=("python",),
            required_role="backend",
            complexity=Complexity.EPIC,
        )

        candidate = scorer.score(agent, subtask)
        assert candidate.score == 0.0

    @pytest.mark.unit
    def test_min_score_property(self) -> None:
        """min_score is accessible."""
        scorer = AgentTaskScorer(min_score=0.3)
        assert scorer.min_score == 0.3

    @pytest.mark.unit
    def test_no_required_skills(self) -> None:
        """Agent with no required skills gets the role-match score."""
        scorer = AgentTaskScorer()
        agent = _make_agent(role="developer")
        subtask = _make_subtask(
            required_role="developer",
            complexity=Complexity.MEDIUM,
        )

        candidate = scorer.score(agent, subtask)
        # Role match (0.2) only.
        assert candidate.score == pytest.approx(0.2)

    @pytest.mark.unit
    def test_on_leave_agent_scores_zero(self) -> None:
        """ON_LEAVE agent gets score 0.0."""
        scorer = AgentTaskScorer()
        agent = _make_agent(
            primary=("python",),
            status=AgentStatus.ON_LEAVE,
        )
        subtask = _make_subtask(required_skills=("python",))

        candidate = scorer.score(agent, subtask)
        assert candidate.score == 0.0

    # NOTE: "skill in both primary and secondary" is now rejected by the
    # SkillSet model validator at construction time; the scorer's exclusion
    # logic (`secondary_matched = ... - primary_matched`) is retained as
    # defence in depth but is no longer reachable via the public API.

    @pytest.mark.unit
    def test_min_score_negative_rejected(self) -> None:
        """Negative min_score is rejected."""
        with pytest.raises(ValueError, match=r"between 0\.0 and 1\.0"):
            AgentTaskScorer(min_score=-0.5)

    @pytest.mark.unit
    def test_min_score_above_one_rejected(self) -> None:
        """min_score above 1.0 is rejected."""
        with pytest.raises(ValueError, match=r"between 0\.0 and 1\.0"):
            AgentTaskScorer(min_score=1.5)

    @pytest.mark.unit
    def test_proficiency_weighted_primary(self) -> None:
        """Primary skill proficiency linearly scales the tier contribution."""
        scorer = AgentTaskScorer()
        agent = _make_agent(
            primary=(Skill(id="python", name="Python", proficiency=0.5),),
        )
        subtask = _make_subtask(required_skills=("python",))

        candidate = scorer.score(agent, subtask)
        # primary contribution = 0.5 (proficiency) / 1 (required) * 0.4 = 0.2
        assert candidate.score == pytest.approx(0.2)
        assert "python" in candidate.matched_skills

    @pytest.mark.unit
    def test_proficiency_weighted_secondary(self) -> None:
        """Secondary skill proficiency linearly scales the tier contribution."""
        scorer = AgentTaskScorer()
        agent = _make_agent(
            secondary=(Skill(id="python", name="Python", proficiency=0.5),),
        )
        subtask = _make_subtask(required_skills=("python",))

        candidate = scorer.score(agent, subtask)
        # secondary contribution = 0.5 / 1 * 0.2 = 0.1
        assert candidate.score == pytest.approx(0.1)

    @pytest.mark.unit
    def test_default_proficiency_matches_legacy_score(self) -> None:
        """Default ``proficiency=1.0`` reproduces legacy boolean-match score."""
        scorer = AgentTaskScorer()
        agent = _make_agent(
            primary=("python",),
        )
        subtask = _make_subtask(required_skills=("python",))

        candidate = scorer.score(agent, subtask)
        # Full primary match at proficiency 1.0 = 0.4 (no other contributions)
        assert candidate.score == pytest.approx(0.4)

    @pytest.mark.unit
    def test_higher_proficiency_outranks_lower(self) -> None:
        """Quality-aware routing: higher proficiency wins ties."""
        scorer = AgentTaskScorer()
        high = _make_agent(
            primary=(Skill(id="python", name="Python", proficiency=0.9),),
        )
        low = _make_agent(
            primary=(Skill(id="python", name="Python", proficiency=0.3),),
        )
        subtask = _make_subtask(required_skills=("python",))

        assert scorer.score(high, subtask).score > scorer.score(low, subtask).score

    @pytest.mark.unit
    def test_proficiency_zero_contributes_nothing(self) -> None:
        """An agent with ``proficiency=0.0`` gets no tier contribution.

        Guards against silent score inflation when a skill exists on the
        agent only as a placeholder.  The skill id still registers as
        ``matched`` (it was in ``required_skills``) but the proficiency
        sum is 0 so ``primary_contrib`` is 0.
        """
        scorer = AgentTaskScorer()
        agent = _make_agent(
            primary=(Skill(id="python", name="Python", proficiency=0.0),),
        )
        subtask = _make_subtask(required_skills=("python",))

        candidate = scorer.score(agent, subtask)
        assert candidate.score == pytest.approx(0.0)
        assert "python" in candidate.matched_skills

    @pytest.mark.unit
    def test_tag_match_adds_bonus(self) -> None:
        """Tag match adds +0.1 when all required tags are covered."""
        scorer = AgentTaskScorer()
        agent = _make_agent(
            primary=(Skill(id="python", name="Python", tags=("backend", "async")),),
        )
        subtask = _make_subtask(
            required_skills=("python",),
            required_tags=("backend",),
        )
        candidate = scorer.score(agent, subtask)
        # primary (0.4) + tag (0.1) = 0.5
        assert candidate.score == pytest.approx(0.5)

    @pytest.mark.unit
    def test_tag_match_requires_full_coverage(self) -> None:
        """Tag match only fires when EVERY required tag is present."""
        scorer = AgentTaskScorer()
        agent = _make_agent(
            primary=(Skill(id="python", name="Python", tags=("backend",)),),
        )
        subtask = _make_subtask(
            required_skills=("python",),
            required_tags=("backend", "realtime"),
        )
        candidate = scorer.score(agent, subtask)
        # primary only, no tag bonus
        assert candidate.score == pytest.approx(0.4)

    @pytest.mark.unit
    def test_tag_match_skipped_without_required_tags(self) -> None:
        """Tag match tier is silent when required_tags is empty."""
        scorer = AgentTaskScorer()
        agent = _make_agent(
            primary=(Skill(id="python", name="Python", tags=("backend",)),),
        )
        subtask = _make_subtask(required_skills=("python",))
        candidate = scorer.score(agent, subtask)
        assert candidate.score == pytest.approx(0.4)

    @pytest.mark.unit
    def test_tag_match_skipped_without_matched_skills(self) -> None:
        """Tag match does not fire when no skills were matched."""
        scorer = AgentTaskScorer()
        agent = _make_agent(
            primary=(Skill(id="rust", name="Rust", tags=("backend",)),),
        )
        subtask = _make_subtask(
            required_skills=("python",),
            required_tags=("backend",),
        )
        candidate = scorer.score(agent, subtask)
        # No primary match (different id), so no tags to consider.
        assert candidate.score == 0.0

    @pytest.mark.unit
    def test_tag_match_fires_on_secondary_skill_tags(self) -> None:
        """Tag bonus considers tags on matched SECONDARY skills, not only primary."""
        scorer = AgentTaskScorer()
        agent = _make_agent(
            secondary=(Skill(id="sql", name="SQL", tags=("database",)),),
        )
        subtask = _make_subtask(
            required_skills=("sql",),
            required_tags=("database",),
        )
        candidate = scorer.score(agent, subtask)
        # Secondary contrib (1.0/1 * 0.2) + tag bonus (0.1) = 0.3
        assert candidate.score == pytest.approx(0.3)

    @pytest.mark.unit
    def test_tag_match_unions_across_primary_and_secondary(self) -> None:
        """Required tags may be covered by the union of tags on matched tiers."""
        scorer = AgentTaskScorer()
        agent = _make_agent(
            primary=(Skill(id="python", name="Python", tags=("backend",)),),
            secondary=(Skill(id="pytest", name="pytest", tags=("qa",)),),
        )
        subtask = _make_subtask(
            required_skills=("python", "pytest"),
            required_tags=("backend", "qa"),
        )
        candidate = scorer.score(agent, subtask)
        # primary (1.0/2 * 0.4 = 0.2) + secondary (1.0/2 * 0.2 = 0.1)
        # + tag bonus (0.1) = 0.4
        assert candidate.score == pytest.approx(0.4)


class TestRoutingScorerConfigInjection:
    """Verify operator-tunable weights flow through the scorer."""

    @pytest.mark.unit
    def test_custom_primary_skill_weight_changes_score(self) -> None:
        """Doubling primary_skill_weight roughly doubles the primary contribution."""
        config = RoutingScorerConfig(primary_skill_weight=0.8)
        scorer = AgentTaskScorer(config=config)
        agent = _make_agent(primary=("python",))
        subtask = _make_subtask(required_skills=("python",), complexity=Complexity.EPIC)
        candidate = scorer.score(agent, subtask)
        # primary (1.0/1 * 0.8 = 0.8); no role match.
        assert candidate.score == pytest.approx(0.8)

    @pytest.mark.unit
    def test_custom_role_match_bonus(self) -> None:
        config = RoutingScorerConfig(role_match_bonus=0.5)
        scorer = AgentTaskScorer(config=config)
        agent = _make_agent(role="qa-engineer")
        subtask = _make_subtask(required_role="qa-engineer", complexity=Complexity.EPIC)
        candidate = scorer.score(agent, subtask)
        # role match (0.5); no skill bonuses on top.
        assert candidate.score == pytest.approx(0.5)

    @pytest.mark.unit
    def test_custom_min_score_overrides_default(self) -> None:
        config = RoutingScorerConfig(min_score=0.5)
        scorer = AgentTaskScorer(config=config)
        assert scorer.min_score == pytest.approx(0.5)

    @pytest.mark.unit
    def test_explicit_min_score_kwarg_overrides_config(self) -> None:
        """``min_score`` kwarg wins over ``config.min_score``."""
        config = RoutingScorerConfig(min_score=0.5)
        scorer = AgentTaskScorer(min_score=0.3, config=config)
        assert scorer.min_score == pytest.approx(0.3)

    @pytest.mark.unit
    def test_default_config_matches_historical_values(self) -> None:
        """Default ``RoutingScorerConfig`` reproduces legacy weights."""
        config = RoutingScorerConfig()
        assert config.primary_skill_weight == pytest.approx(0.4)
        assert config.secondary_skill_weight == pytest.approx(0.2)
        assert config.tag_match_bonus == pytest.approx(0.1)
        assert config.role_match_bonus == pytest.approx(0.2)
        assert config.min_score == pytest.approx(0.1)

    @pytest.mark.unit
    def test_from_bridge_config_extracts_routing_subset(self) -> None:
        """Bridge-config projection wires every routing field."""
        from synthorg.settings.bridge_configs import EngineBridgeConfig

        bridge = EngineBridgeConfig(
            routing_weight_primary_skill=0.55,
            routing_weight_secondary_skill=0.15,
            routing_weight_tag_match_bonus=0.05,
            routing_weight_role_match_bonus=0.25,
            routing_min_score=0.2,
        )
        config = RoutingScorerConfig.from_bridge_config(bridge)
        assert config.primary_skill_weight == pytest.approx(0.55)
        assert config.secondary_skill_weight == pytest.approx(0.15)
        assert config.tag_match_bonus == pytest.approx(0.05)
        assert config.role_match_bonus == pytest.approx(0.25)
        assert config.min_score == pytest.approx(0.2)

    @pytest.mark.unit
    def test_weight_sum_validator_stays_silent_on_stock_defaults(self) -> None:
        """The shipped defaults must not warn about themselves.

        ``0.4 + 0.2 + 0.1 + 0.2`` is 0.9000000000000001 in binary floating
        point, so an exact ``>`` against the documented 0.9 envelope fires on
        every construction of the default config.
        """
        with capture_logs() as logs:
            RoutingScorerConfig()
        warns = [
            entry
            for entry in logs
            if entry.get("event") == TASK_ROUTING_SCORER_INVALID_CONFIG
        ]
        assert not warns, f"stock defaults warned about themselves (logs={logs})"

    @pytest.mark.unit
    def test_weight_sum_validator_warns_on_excessive_weights(self) -> None:
        """Sum above the documented ceiling logs a warning but does not raise."""
        # All weights at 0.4 -> sum = 1.6, well above both the
        # documented envelope (0.9) and the hard ceiling (1.1).
        with capture_logs() as logs:
            config = RoutingScorerConfig(
                primary_skill_weight=0.4,
                secondary_skill_weight=0.4,
                tag_match_bonus=0.4,
                role_match_bonus=0.4,
            )
        assert config.primary_skill_weight == pytest.approx(0.4)
        warns = [
            entry
            for entry in logs
            if entry.get("event") == TASK_ROUTING_SCORER_INVALID_CONFIG
            and entry.get("log_level") == "warning"
        ]
        assert len(warns) == 1, (
            "expected exactly one TASK_ROUTING_SCORER_INVALID_CONFIG warning, "
            f"got {len(warns)} (logs={logs})"
        )
        # Assert the whole payload against the documented envelope rather
        # than ``weight_sum`` alone: the operator reads the ceilings to
        # know how far out of band the configuration is, and a warning
        # that dropped them would still pass a sum-only assertion.
        assert warns[0]["weight_sum"] == pytest.approx(1.6)
        assert warns[0]["documented_max"] == pytest.approx(0.9)
        assert warns[0]["warn_ceiling"] == pytest.approx(1.1)
        assert warns[0]["reason"] == "routing weights exceed the documented maximum"
        # ``error=`` is reserved for redacted exception text; a
        # hand-written description must never travel under it.
        assert "error" not in warns[0]

    @pytest.mark.unit
    def test_weight_sum_validator_warns_in_envelope_to_ceiling_band(self) -> None:
        """Sum in (documented_max, hard_ceiling] still triggers a warning.

        Pins the widened-warning behaviour so a future regression that
        narrows the condition back to "only above the hard ceiling"
        gets caught here. ``0.4 + 0.3 + 0.1 + 0.2 = 1.0`` sits
        in the ``(0.9, 1.1]`` band that the previous condition
        silently let through.
        """
        with capture_logs() as logs:
            config = RoutingScorerConfig(
                primary_skill_weight=0.4,
                secondary_skill_weight=0.3,
                tag_match_bonus=0.1,
                role_match_bonus=0.2,
            )
        assert config.primary_skill_weight == pytest.approx(0.4)
        warns = [
            entry
            for entry in logs
            if entry.get("event") == TASK_ROUTING_SCORER_INVALID_CONFIG
            and entry.get("log_level") == "warning"
        ]
        assert len(warns) == 1, (
            "expected exactly one TASK_ROUTING_SCORER_INVALID_CONFIG warning "
            f"for sum 1.0, got {len(warns)} (logs={logs})"
        )
        assert warns[0]["weight_sum"] == pytest.approx(1.0)
