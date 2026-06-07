"""Unit tests for project team filtering in TaskAssignmentService."""

from datetime import date

import pytest

from synthorg.core.agent import AgentIdentity, ModelConfig, SkillSet
from synthorg.core.enums import TaskType
from synthorg.core.role import Skill
from synthorg.core.task import Task
from synthorg.engine.assignment.models import AssignmentRequest
from synthorg.engine.assignment.pool_filters import IdentityPoolFilter
from synthorg.engine.assignment.rankers import ScoreDescendingRanker
from synthorg.engine.assignment.scoring_based import ScoringBasedAssignmentStrategy
from synthorg.engine.assignment.service import TaskAssignmentService
from synthorg.engine.routing.scorer import AgentTaskScorer
from synthorg.hr.seniority import SeniorityLevel
from tests._shared import sid

pytestmark = pytest.mark.unit


def _model_config() -> ModelConfig:
    return ModelConfig(provider="test-provider", model_id="test-small-001")


def _make_agent(
    name: str,
    *,
    level: SeniorityLevel = SeniorityLevel.MID,
) -> AgentIdentity:
    return AgentIdentity(
        name=name,
        role="Developer",
        department="Engineering",
        level=level,
        model=_model_config(),
        hiring_date=date(2026, 1, 1),
        skills=SkillSet(primary=(Skill(id="python", name="python"),)),
    )


def _make_task(**overrides: object) -> Task:
    defaults: dict[str, object] = {
        "id": sid("task-001"),
        "title": "Test task",
        "description": "A test task",
        "type": TaskType.DEVELOPMENT,
        "project": "proj-001",
        "created_by": "manager",
    }
    defaults.update(overrides)
    return Task(**defaults)  # type: ignore[arg-type]


def _make_service() -> TaskAssignmentService:
    scorer = AgentTaskScorer()
    strategy = ScoringBasedAssignmentStrategy(
        name="role_based",
        scorer=scorer,
        pool_filter=IdentityPoolFilter(),
        ranker=ScoreDescendingRanker(),
    )
    return TaskAssignmentService(strategy)


class TestProjectTeamFiltering:
    """Tests for project_team filtering in assignment service."""

    def test_empty_project_team_no_filtering(self) -> None:
        """Empty project_team means all agents are considered."""
        agent_a = _make_agent("Alice")
        agent_b = _make_agent("Bob")
        service = _make_service()

        request = AssignmentRequest(
            task=_make_task(),
            available_agents=(agent_a, agent_b),
            project_team=(),
        )
        result = service.assign(request)
        # Both agents were available; strategy picks one
        assert result.selected is not None

    def test_project_team_filters_to_members(self) -> None:
        """Only agents in project_team are considered."""
        alice = _make_agent("Alice")
        bob = _make_agent("Bob")
        service = _make_service()

        request = AssignmentRequest(
            task=_make_task(),
            available_agents=(alice, bob),
            project_team=(str(alice.id),),
        )
        result = service.assign(request)
        assert result.selected is not None
        assert result.selected.agent_identity.id == alice.id

    def test_no_overlap_returns_none(self) -> None:
        """No agents in team -> strategy gets empty pool -> selected=None."""
        alice = _make_agent("Alice")
        service = _make_service()

        request = AssignmentRequest(
            task=_make_task(),
            available_agents=(alice,),
            project_team=("nonexistent-agent-id",),
        )
        result = service.assign(request)
        assert result.selected is None

    def test_partial_overlap(self) -> None:
        """Only overlapping agents pass through."""
        alice = _make_agent("Alice")
        bob = _make_agent("Bob")
        carol = _make_agent("Carol")
        service = _make_service()

        request = AssignmentRequest(
            task=_make_task(),
            available_agents=(alice, bob, carol),
            project_team=(str(bob.id), str(carol.id)),
        )
        result = service.assign(request)
        assert result.selected is not None
        assert result.selected.agent_identity.id in {bob.id, carol.id}

    def test_default_project_team_is_empty(self) -> None:
        """AssignmentRequest defaults to empty project_team."""
        alice = _make_agent("Alice")
        request = AssignmentRequest(
            task=_make_task(),
            available_agents=(alice,),
        )
        assert request.project_team == ()
