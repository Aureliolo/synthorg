"""Tests for agent-task scorer."""

from datetime import date
from uuid import uuid4

import pytest

from ai_company.core.agent import AgentIdentity, ModelConfig, SkillSet
from ai_company.core.enums import AgentStatus, SeniorityLevel
from ai_company.engine.decomposition.models import SubtaskDefinition
from ai_company.engine.routing.scorer import AgentTaskScorer


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
    complexity: str = "medium",
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
        subtask = _make_subtask(complexity="complex")

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
            complexity="medium",
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
            complexity="epic",
        )

        candidate = scorer.score(agent, subtask)
        assert candidate.score < 0.2

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
            complexity="medium",
        )

        candidate = scorer.score(agent, subtask)
        # Role match (0.2) + seniority alignment (0.2) = 0.4
        assert candidate.score == pytest.approx(0.4)
