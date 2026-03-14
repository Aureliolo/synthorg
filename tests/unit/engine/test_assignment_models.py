"""Unit tests for task assignment domain models."""

from datetime import date

import pytest
from pydantic import ValidationError

from synthorg.core.agent import AgentIdentity, ModelConfig, SkillSet
from synthorg.core.enums import (
    SeniorityLevel,
    TaskType,
)
from synthorg.core.task import Task
from synthorg.engine.assignment.models import (
    AgentWorkload,
    AssignmentCandidate,
    AssignmentRequest,
    AssignmentResult,
)

pytestmark = pytest.mark.unit


def _model_config() -> ModelConfig:
    return ModelConfig(provider="test-provider", model_id="test-small-001")


def _make_agent(
    name: str,
    *,
    level: SeniorityLevel = SeniorityLevel.MID,
    primary_skills: tuple[str, ...] = (),
) -> AgentIdentity:
    return AgentIdentity(
        name=name,
        role="Developer",
        department="Engineering",
        level=level,
        model=_model_config(),
        hiring_date=date(2026, 1, 1),
        skills=SkillSet(primary=primary_skills),
    )


def _make_task(**overrides: object) -> Task:
    defaults: dict[str, object] = {
        "id": "task-001",
        "title": "Test task",
        "description": "A test task",
        "type": TaskType.DEVELOPMENT,
        "project": "proj-001",
        "created_by": "manager",
    }
    defaults.update(overrides)
    return Task(**defaults)  # type: ignore[arg-type]


class TestAgentWorkload:
    """AgentWorkload validation tests."""

    def test_valid_workload(self) -> None:
        """Workload with valid values constructs successfully."""
        workload = AgentWorkload(
            agent_id="agent-1",
            active_task_count=3,
            total_cost_usd=1.50,
        )
        assert workload.agent_id == "agent-1"
        assert workload.active_task_count == 3
        assert workload.total_cost_usd == 1.50

    def test_negative_task_count_rejected(self) -> None:
        """Negative active_task_count raises ValidationError."""
        with pytest.raises(ValidationError, match="active_task_count"):
            AgentWorkload(
                agent_id="agent-1",
                active_task_count=-1,
            )

    def test_negative_cost_rejected(self) -> None:
        """Negative total_cost_usd raises ValidationError."""
        with pytest.raises(ValidationError, match="total_cost_usd"):
            AgentWorkload(
                agent_id="agent-1",
                active_task_count=0,
                total_cost_usd=-0.5,
            )

    def test_zero_workload(self) -> None:
        """Zero workload is valid."""
        workload = AgentWorkload(
            agent_id="agent-1",
            active_task_count=0,
            total_cost_usd=0.0,
        )
        assert workload.active_task_count == 0
        assert workload.total_cost_usd == 0.0

    def test_blank_agent_id_rejected(self) -> None:
        """Blank agent_id raises ValidationError."""
        with pytest.raises(ValidationError, match="agent_id"):
            AgentWorkload(agent_id="  ", active_task_count=0)

    @pytest.mark.parametrize(
        "cost",
        [float("nan"), float("inf"), float("-inf")],
        ids=["nan", "inf", "neg_inf"],
    )
    def test_nan_inf_cost_rejected(self, cost: float) -> None:
        """NaN and Inf total_cost_usd values are rejected."""
        with pytest.raises(ValidationError):
            AgentWorkload(
                agent_id="agent-1",
                active_task_count=0,
                total_cost_usd=cost,
            )

    def test_frozen(self) -> None:
        """Workload is immutable."""
        workload = AgentWorkload(agent_id="agent-1", active_task_count=0)
        with pytest.raises(ValidationError):
            workload.active_task_count = 5  # type: ignore[misc]


class TestAssignmentCandidate:
    """AssignmentCandidate validation tests."""

    def test_valid_candidate(self) -> None:
        """Candidate with valid score and agent constructs successfully."""
        agent = _make_agent("dev-1")
        candidate = AssignmentCandidate(
            agent_identity=agent,
            score=0.8,
            matched_skills=("python",),
            reason="Good match",
        )
        assert candidate.score == 0.8
        assert candidate.matched_skills == ("python",)

    def test_score_below_zero_rejected(self) -> None:
        """Score below 0.0 raises ValidationError."""
        agent = _make_agent("dev-1")
        with pytest.raises(ValidationError, match="score"):
            AssignmentCandidate(
                agent_identity=agent,
                score=-0.1,
                reason="Bad",
            )

    def test_score_above_one_rejected(self) -> None:
        """Score above 1.0 raises ValidationError."""
        agent = _make_agent("dev-1")
        with pytest.raises(ValidationError, match="score"):
            AssignmentCandidate(
                agent_identity=agent,
                score=1.1,
                reason="Over",
            )

    def test_blank_reason_rejected(self) -> None:
        """Blank reason raises ValidationError."""
        agent = _make_agent("dev-1")
        with pytest.raises(ValidationError, match="reason"):
            AssignmentCandidate(
                agent_identity=agent,
                score=0.5,
                reason="  ",
            )

    def test_score_boundaries(self) -> None:
        """Scores of exactly 0.0 and 1.0 are valid."""
        agent = _make_agent("dev-1")
        low = AssignmentCandidate(
            agent_identity=agent,
            score=0.0,
            reason="Zero score",
        )
        high = AssignmentCandidate(
            agent_identity=agent,
            score=1.0,
            reason="Perfect score",
        )
        assert low.score == 0.0
        assert high.score == 1.0


class TestAssignmentRequest:
    """AssignmentRequest validation tests."""

    def test_valid_request(self) -> None:
        """Request with task and agents constructs successfully."""
        task = _make_task()
        agent = _make_agent("dev-1")
        request = AssignmentRequest(
            task=task,
            available_agents=(agent,),
        )
        assert request.min_score == 0.1
        assert request.required_skills == ()
        assert request.required_role is None

    def test_min_score_default(self) -> None:
        """Default min_score is 0.1."""
        request = AssignmentRequest(
            task=_make_task(),
            available_agents=(_make_agent("dev-1"),),
        )
        assert request.min_score == 0.1

    @pytest.mark.parametrize(
        "score",
        [-0.1, 1.5],
        ids=["below_zero", "above_one"],
    )
    def test_min_score_out_of_range(self, score: float) -> None:
        """min_score outside [0.0, 1.0] raises ValidationError."""
        with pytest.raises(ValidationError, match="min_score"):
            AssignmentRequest(
                task=_make_task(),
                available_agents=(_make_agent("dev-1"),),
                min_score=score,
            )

    def test_with_required_skills(self) -> None:
        """Request with required_skills is valid."""
        request = AssignmentRequest(
            task=_make_task(),
            available_agents=(_make_agent("dev-1"),),
            required_skills=("python", "api-design"),
            required_role="Backend Developer",
        )
        assert request.required_skills == ("python", "api-design")
        assert request.required_role == "Backend Developer"

    def test_with_workloads(self) -> None:
        """Request with workloads is valid."""
        agent = _make_agent("dev-1")
        workload = AgentWorkload(
            agent_id=str(agent.id),
            active_task_count=2,
        )
        request = AssignmentRequest(
            task=_make_task(),
            available_agents=(agent,),
            workloads=(workload,),
        )
        assert len(request.workloads) == 1


class TestAssignmentResult:
    """AssignmentResult validation tests."""

    def test_result_with_selected(self) -> None:
        """Result with selected candidate constructs successfully."""
        agent = _make_agent("dev-1")
        candidate = AssignmentCandidate(
            agent_identity=agent,
            score=0.9,
            reason="Top match",
        )
        result = AssignmentResult(
            task_id="task-001",
            strategy_used="role_based",
            selected=candidate,
            reason="Best match found",
        )
        assert result.selected is not None
        assert result.selected.score == 0.9
        assert result.alternatives == ()

    def test_result_without_selected(self) -> None:
        """Result with no selected candidate is valid."""
        result = AssignmentResult(
            task_id="task-001",
            strategy_used="role_based",
            reason="No eligible agents",
        )
        assert result.selected is None
        assert result.alternatives == ()

    def test_result_with_alternatives(self) -> None:
        """Result with alternatives constructs successfully."""
        agent1 = _make_agent("dev-1")
        agent2 = _make_agent("dev-2")
        selected = AssignmentCandidate(
            agent_identity=agent1,
            score=0.9,
            reason="Top match",
        )
        alt = AssignmentCandidate(
            agent_identity=agent2,
            score=0.7,
            reason="Second match",
        )
        result = AssignmentResult(
            task_id="task-001",
            strategy_used="role_based",
            selected=selected,
            alternatives=(alt,),
            reason="Best match found",
        )
        assert len(result.alternatives) == 1
        assert result.alternatives[0].score == 0.7

    def test_selected_not_in_alternatives(self) -> None:
        """Selected candidate must not appear in alternatives."""
        agent = _make_agent("dev-1")
        candidate = AssignmentCandidate(
            agent_identity=agent,
            score=0.9,
            reason="Top match",
        )
        with pytest.raises(ValidationError, match="also appears in alternatives"):
            AssignmentResult(
                task_id="task-001",
                strategy_used="role_based",
                selected=candidate,
                alternatives=(candidate,),
                reason="Duplicate",
            )

    def test_selected_none_with_empty_alternatives_is_valid(self) -> None:
        """Result with selected=None and empty alternatives is valid."""
        result = AssignmentResult(
            task_id="task-001",
            strategy_used="role_based",
            reason="No agents found",
        )
        assert result.selected is None
        assert result.alternatives == ()

    def test_frozen(self) -> None:
        """Result is immutable."""
        result = AssignmentResult(
            task_id="task-001",
            strategy_used="manual",
            reason="Done",
        )
        with pytest.raises(ValidationError):
            result.task_id = "other"  # type: ignore[misc]

    def test_empty_available_agents_rejected(self) -> None:
        """Empty available_agents tuple raises ValidationError."""
        with pytest.raises(ValidationError, match="available_agents"):
            AssignmentRequest(
                task=_make_task(),
                available_agents=(),
            )

    def test_duplicate_agent_ids_rejected(self) -> None:
        """Duplicate agent IDs in available_agents raises ValidationError."""
        agent = _make_agent("dev-1")
        with pytest.raises(ValidationError, match="Duplicate agent IDs"):
            AssignmentRequest(
                task=_make_task(),
                available_agents=(agent, agent),
            )

    def test_duplicate_workload_agent_ids_rejected(self) -> None:
        """Duplicate agent_id in workloads raises ValidationError."""
        agent = _make_agent("dev-1")
        workload = AgentWorkload(
            agent_id=str(agent.id),
            active_task_count=1,
        )
        with pytest.raises(ValidationError, match="Duplicate agent_id"):
            AssignmentRequest(
                task=_make_task(),
                available_agents=(agent,),
                workloads=(workload, workload),
            )
