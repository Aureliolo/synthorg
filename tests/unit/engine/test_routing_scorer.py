"""Tests for agent-task scorer."""

from datetime import date
from uuid import uuid4

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig, SkillSet
from synthorg.core.enums import AgentStatus, Complexity, SeniorityLevel
from synthorg.engine.decomposition.models import SubtaskDefinition
from synthorg.engine.routing.scorer import AgentTaskScorer


def _make_agent(
    *,
    primary: tuple[str, ...] = (),
    secondary: tuple[str, ...] = (),
    role: str = "developer",
    level: SeniorityLevel = SeniorityLevel.MID,
    status: AgentStatus = AgentStatus.ACTIVE,
) -> AgentIdentity:
    """Helper to create an agent with specific skills."""
    return AgentIdentity(
        id=uuid4(),
        name="Test Agent",
        role=role,
        department="Engineering",
        level=level,
        skills=SkillSet(primary=primary, secondary=secondary),
        model=ModelConfig(provider="test-provider", model_id="test-model-001"),
        hiring_date=date(2026, 1, 1),
        status=status,
    )


def _make_subtask(
    *,
    required_skills: tuple[str, ...] = (),
    required_role: str | None = None,
    complexity: Complexity = Complexity.MEDIUM,
) -> SubtaskDefinition:
    """Helper to create a subtask with requirements."""
    return SubtaskDefinition(
        id="sub-test",
        title="Test Subtask",
        description="A test subtask",
        required_skills=required_skills,
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
    def test_seniority_complexity_alignment(self) -> None:
        """Seniority-complexity alignment adds to score."""
        scorer = AgentTaskScorer()
        agent = _make_agent(level=SeniorityLevel.SENIOR)
        subtask = _make_subtask(complexity=Complexity.COMPLEX)

        candidate = scorer.score(agent, subtask)
        assert candidate.score >= 0.2

    @pytest.mark.unit
    def test_score_capped_at_one(self) -> None:
        """Score is capped at 1.0."""
        scorer = AgentTaskScorer()
        agent = _make_agent(
            primary=("python", "sql"),
            secondary=("testing",),
            role="developer",
            level=SeniorityLevel.MID,
        )
        subtask = _make_subtask(
            required_skills=("python",),
            required_role="developer",
            complexity=Complexity.MEDIUM,
        )

        candidate = scorer.score(agent, subtask)
        assert candidate.score <= 1.0

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
        """Agent with no required skills gets seniority + role scores."""
        scorer = AgentTaskScorer()
        agent = _make_agent(level=SeniorityLevel.MID, role="developer")
        subtask = _make_subtask(
            required_role="developer",
            complexity=Complexity.MEDIUM,
        )

        candidate = scorer.score(agent, subtask)
        # Role match (0.2) + seniority alignment (0.2) = 0.4
        assert candidate.score == pytest.approx(0.4)

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

    @pytest.mark.unit
    @pytest.mark.parametrize(
        ("level", "complexity"),
        [
            (SeniorityLevel.JUNIOR, Complexity.SIMPLE),
            (SeniorityLevel.MID, Complexity.MEDIUM),
            (SeniorityLevel.SENIOR, Complexity.COMPLEX),
            (SeniorityLevel.LEAD, Complexity.EPIC),
            (SeniorityLevel.PRINCIPAL, Complexity.EPIC),
            (SeniorityLevel.DIRECTOR, Complexity.EPIC),
            (SeniorityLevel.VP, Complexity.EPIC),
            (SeniorityLevel.C_SUITE, Complexity.EPIC),
        ],
        ids=[
            "junior-simple",
            "mid-medium",
            "senior-complex",
            "lead-epic",
            "principal-epic",
            "director-epic",
            "vp-epic",
            "c_suite-epic",
        ],
    )
    def test_seniority_complexity_parametrized(
        self, level: SeniorityLevel, complexity: Complexity
    ) -> None:
        """Seniority-complexity alignment works for various levels."""
        scorer = AgentTaskScorer()
        agent = _make_agent(level=level)
        subtask = _make_subtask(complexity=complexity)

        candidate = scorer.score(agent, subtask)
        assert candidate.score >= 0.2

    @pytest.mark.unit
    def test_skill_in_both_primary_and_secondary(self) -> None:
        """Skill in both primary and secondary is not double-counted."""
        scorer = AgentTaskScorer()
        agent = _make_agent(primary=("python",), secondary=("python",))
        subtask = _make_subtask(required_skills=("python",))

        candidate = scorer.score(agent, subtask)
        # Primary match gives 0.4, secondary should not add 0.2
        # plus seniority alignment (MID + MEDIUM = 0.2) = 0.6
        assert candidate.score == pytest.approx(0.6)
        assert candidate.matched_skills.count("python") == 1

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
