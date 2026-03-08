"""Unit tests for task assignment strategies."""

from datetime import date

import pytest

from ai_company.core.agent import AgentIdentity, ModelConfig, SkillSet
from ai_company.core.enums import (
    AgentStatus,
    Complexity,
    SeniorityLevel,
    TaskType,
)
from ai_company.core.task import Task
from ai_company.engine.assignment.models import (
    AgentWorkload,
    AssignmentRequest,
)
from ai_company.engine.assignment.protocol import TaskAssignmentStrategy
from ai_company.engine.assignment.strategies import (
    STRATEGY_MAP,
    STRATEGY_NAME_LOAD_BALANCED,
    STRATEGY_NAME_MANUAL,
    STRATEGY_NAME_ROLE_BASED,
    LoadBalancedAssignmentStrategy,
    ManualAssignmentStrategy,
    RoleBasedAssignmentStrategy,
)
from ai_company.engine.errors import NoEligibleAgentError, TaskAssignmentError
from ai_company.engine.routing.scorer import AgentTaskScorer

pytestmark = pytest.mark.unit


def _model_config() -> ModelConfig:
    return ModelConfig(provider="test-provider", model_id="test-small-001")


def _make_agent(  # noqa: PLR0913
    name: str,
    *,
    level: SeniorityLevel = SeniorityLevel.MID,
    primary_skills: tuple[str, ...] = (),
    secondary_skills: tuple[str, ...] = (),
    role: str = "Developer",
    status: AgentStatus = AgentStatus.ACTIVE,
) -> AgentIdentity:
    return AgentIdentity(
        name=name,
        role=role,
        department="Engineering",
        level=level,
        model=_model_config(),
        hiring_date=date(2026, 1, 1),
        skills=SkillSet(primary=primary_skills, secondary=secondary_skills),
        status=status,
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


class TestManualAssignmentStrategy:
    """ManualAssignmentStrategy tests."""

    def test_success_with_valid_assigned_to(self) -> None:
        """Manual assignment succeeds when assigned_to matches an active agent."""
        strategy = ManualAssignmentStrategy()
        agent = _make_agent("dev-1")
        task = _make_task(
            assigned_to=str(agent.id),
            status="assigned",
        )
        request = AssignmentRequest(
            task=task,
            available_agents=(agent,),
        )

        result = strategy.assign(request)

        assert result.selected is not None
        assert result.selected.score == 1.0
        assert result.selected.agent_identity.name == "dev-1"
        assert result.strategy_used == "manual"

    def test_error_when_assigned_to_is_none(self) -> None:
        """Manual assignment raises TaskAssignmentError when assigned_to is None."""
        strategy = ManualAssignmentStrategy()
        task = _make_task()
        request = AssignmentRequest(
            task=task,
            available_agents=(_make_agent("dev-1"),),
        )

        with pytest.raises(TaskAssignmentError, match="assigned_to"):
            strategy.assign(request)

    def test_error_when_agent_not_in_pool(self) -> None:
        """Manual assignment raises NoEligibleAgentError when agent not found."""
        strategy = ManualAssignmentStrategy()
        agent_in_pool = _make_agent("dev-1")
        task = _make_task(
            assigned_to="nonexistent-agent-id",
            status="assigned",
        )
        request = AssignmentRequest(
            task=task,
            available_agents=(agent_in_pool,),
        )

        with pytest.raises(NoEligibleAgentError, match="not found"):
            strategy.assign(request)

    @pytest.mark.parametrize(
        "status",
        [AgentStatus.ON_LEAVE, AgentStatus.TERMINATED],
        ids=["on_leave", "terminated"],
    )
    def test_inactive_agent_rejected(self, status: AgentStatus) -> None:
        """Manual assignment rejects agents that are not ACTIVE."""
        strategy = ManualAssignmentStrategy()
        agent = _make_agent("dev-1", status=status)
        task = _make_task(
            assigned_to=str(agent.id),
            status="assigned",
        )
        request = AssignmentRequest(
            task=task,
            available_agents=(agent,),
        )

        with pytest.raises(NoEligibleAgentError, match=status.value):
            strategy.assign(request)

    def test_name_property(self) -> None:
        """Strategy name is 'manual'."""
        assert ManualAssignmentStrategy().name == "manual"


class TestRoleBasedAssignmentStrategy:
    """RoleBasedAssignmentStrategy tests."""

    def test_best_scoring_agent_selected(self) -> None:
        """Highest-scoring agent is selected."""
        scorer = AgentTaskScorer()
        strategy = RoleBasedAssignmentStrategy(scorer)

        # Backend dev has matching skills
        backend = _make_agent(
            "backend",
            primary_skills=("python", "api-design"),
            level=SeniorityLevel.MID,
        )
        # Frontend dev has non-matching skills
        frontend = _make_agent(
            "frontend",
            primary_skills=("typescript", "react"),
            level=SeniorityLevel.MID,
        )

        task = _make_task(estimated_complexity=Complexity.MEDIUM)
        request = AssignmentRequest(
            task=task,
            available_agents=(backend, frontend),
            required_skills=("python", "api-design"),
        )

        result = strategy.assign(request)

        assert result.selected is not None
        assert result.selected.agent_identity.name == "backend"
        assert result.selected.score > 0.0

    def test_alternatives_populated(self) -> None:
        """Non-selected viable agents appear in alternatives."""
        scorer = AgentTaskScorer()
        strategy = RoleBasedAssignmentStrategy(scorer)

        agent1 = _make_agent(
            "dev-1",
            primary_skills=("python",),
            level=SeniorityLevel.MID,
        )
        agent2 = _make_agent(
            "dev-2",
            primary_skills=("python",),
            level=SeniorityLevel.MID,
        )

        task = _make_task(estimated_complexity=Complexity.MEDIUM)
        request = AssignmentRequest(
            task=task,
            available_agents=(agent1, agent2),
            required_skills=("python",),
        )

        result = strategy.assign(request)

        assert result.selected is not None
        assert len(result.alternatives) == 1

    def test_no_viable_agents_returns_none_selected(self) -> None:
        """Returns selected=None when no agents score above threshold."""
        scorer = AgentTaskScorer(min_score=0.1)
        strategy = RoleBasedAssignmentStrategy(scorer)

        # Agent with completely non-matching skills
        agent = _make_agent(
            "qa",
            primary_skills=("testing",),
            level=SeniorityLevel.JUNIOR,
        )

        task = _make_task(estimated_complexity=Complexity.EPIC)
        request = AssignmentRequest(
            task=task,
            available_agents=(agent,),
            required_skills=("python", "api-design", "databases"),
            required_role="Backend Developer",
            min_score=0.5,
        )

        result = strategy.assign(request)

        assert result.selected is None
        assert "threshold" in result.reason

    def test_no_required_skills_seniority_only_fallback(self) -> None:
        """Without required_skills, scoring falls back to seniority."""
        scorer = AgentTaskScorer()
        strategy = RoleBasedAssignmentStrategy(scorer)

        agent = _make_agent("dev-1", level=SeniorityLevel.MID)
        task = _make_task(estimated_complexity=Complexity.MEDIUM)
        request = AssignmentRequest(
            task=task,
            available_agents=(agent,),
        )

        result = strategy.assign(request)

        # Should still produce a result based on seniority alignment
        assert result.selected is not None
        assert result.selected.score > 0.0

    def test_name_property(self) -> None:
        """Strategy name is 'role_based'."""
        scorer = AgentTaskScorer()
        assert RoleBasedAssignmentStrategy(scorer).name == "role_based"


class TestLoadBalancedAssignmentStrategy:
    """LoadBalancedAssignmentStrategy tests."""

    def test_lowest_workload_wins(self) -> None:
        """Agent with lowest workload is selected."""
        scorer = AgentTaskScorer()
        strategy = LoadBalancedAssignmentStrategy(scorer)

        busy = _make_agent(
            "busy-dev",
            primary_skills=("python",),
            level=SeniorityLevel.MID,
        )
        idle = _make_agent(
            "idle-dev",
            primary_skills=("python",),
            level=SeniorityLevel.MID,
        )

        task = _make_task(estimated_complexity=Complexity.MEDIUM)
        request = AssignmentRequest(
            task=task,
            available_agents=(busy, idle),
            required_skills=("python",),
            workloads=(
                AgentWorkload(
                    agent_id=str(busy.id),
                    active_task_count=5,
                ),
                AgentWorkload(
                    agent_id=str(idle.id),
                    active_task_count=1,
                ),
            ),
        )

        result = strategy.assign(request)

        assert result.selected is not None
        assert result.selected.agent_identity.name == "idle-dev"

    def test_ties_broken_by_score(self) -> None:
        """Equal workload is broken by higher score."""
        scorer = AgentTaskScorer()
        strategy = LoadBalancedAssignmentStrategy(scorer)

        # Both have same workload, but better_dev has matching skills
        better_dev = _make_agent(
            "better-dev",
            primary_skills=("python", "api-design"),
            role="Backend Developer",
            level=SeniorityLevel.MID,
        )
        other_dev = _make_agent(
            "other-dev",
            primary_skills=("testing",),
            level=SeniorityLevel.MID,
        )

        task = _make_task(estimated_complexity=Complexity.MEDIUM)
        request = AssignmentRequest(
            task=task,
            available_agents=(better_dev, other_dev),
            required_skills=("python", "api-design"),
            required_role="Backend Developer",
            workloads=(
                AgentWorkload(
                    agent_id=str(better_dev.id),
                    active_task_count=2,
                ),
                AgentWorkload(
                    agent_id=str(other_dev.id),
                    active_task_count=2,
                ),
            ),
        )

        result = strategy.assign(request)

        assert result.selected is not None
        assert result.selected.agent_identity.name == "better-dev"

    def test_empty_workloads_falls_back_to_capability(self) -> None:
        """Without workloads, falls back to capability-only sorting."""
        scorer = AgentTaskScorer()
        strategy = LoadBalancedAssignmentStrategy(scorer)

        best = _make_agent(
            "best-dev",
            primary_skills=("python", "api-design"),
            level=SeniorityLevel.MID,
        )
        other = _make_agent(
            "other-dev",
            primary_skills=("testing",),
            level=SeniorityLevel.MID,
        )

        task = _make_task(estimated_complexity=Complexity.MEDIUM)
        request = AssignmentRequest(
            task=task,
            available_agents=(best, other),
            required_skills=("python", "api-design"),
        )

        result = strategy.assign(request)

        assert result.selected is not None
        assert result.selected.agent_identity.name == "best-dev"

    @pytest.mark.parametrize(
        ("workloads", "expected_winner"),
        [
            ((0, 3, 5), "dev-0"),
            ((2, 2, 0), "dev-2"),
            ((1, 1, 1), "dev-0"),  # all equal, highest score wins
        ],
        ids=["first-lowest", "last-lowest", "all-equal"],
    )
    def test_parametrized_workload_distributions(
        self,
        workloads: tuple[int, ...],
        expected_winner: str,
    ) -> None:
        """Parametrized test for various workload distributions."""
        scorer = AgentTaskScorer()
        strategy = LoadBalancedAssignmentStrategy(scorer)

        agents = tuple(
            _make_agent(
                f"dev-{i}",
                primary_skills=("python",),
                level=SeniorityLevel.MID,
            )
            for i in range(3)
        )

        task = _make_task(estimated_complexity=Complexity.MEDIUM)
        request = AssignmentRequest(
            task=task,
            available_agents=agents,
            required_skills=("python",),
            workloads=tuple(
                AgentWorkload(
                    agent_id=str(agents[i].id),
                    active_task_count=w,
                )
                for i, w in enumerate(workloads)
            ),
        )

        result = strategy.assign(request)

        assert result.selected is not None
        assert result.selected.agent_identity.name == expected_winner

    def test_no_eligible_returns_none(self) -> None:
        """Returns selected=None when no agents score above threshold."""
        scorer = AgentTaskScorer()
        strategy = LoadBalancedAssignmentStrategy(scorer)

        agent = _make_agent(
            "qa",
            primary_skills=("testing",),
            level=SeniorityLevel.JUNIOR,
        )

        task = _make_task(estimated_complexity=Complexity.EPIC)
        request = AssignmentRequest(
            task=task,
            available_agents=(agent,),
            required_skills=("python", "api-design"),
            required_role="Backend Developer",
            min_score=0.5,
        )

        result = strategy.assign(request)

        assert result.selected is None

    def test_partial_workload_data(self) -> None:
        """Agents without workload entries default to zero workload."""
        scorer = AgentTaskScorer()
        strategy = LoadBalancedAssignmentStrategy(scorer)

        known = _make_agent(
            "known-dev",
            primary_skills=("python",),
            level=SeniorityLevel.MID,
        )
        unknown = _make_agent(
            "unknown-dev",
            primary_skills=("python",),
            level=SeniorityLevel.MID,
        )

        task = _make_task(estimated_complexity=Complexity.MEDIUM)
        request = AssignmentRequest(
            task=task,
            available_agents=(known, unknown),
            required_skills=("python",),
            workloads=(
                AgentWorkload(
                    agent_id=str(known.id),
                    active_task_count=3,
                ),
            ),
        )

        result = strategy.assign(request)

        assert result.selected is not None
        # unknown-dev should win because it defaults to 0 workload
        assert result.selected.agent_identity.name == "unknown-dev"

    def test_name_property(self) -> None:
        """Strategy name is 'load_balanced'."""
        scorer = AgentTaskScorer()
        assert LoadBalancedAssignmentStrategy(scorer).name == "load_balanced"


class TestStrategyMap:
    """STRATEGY_MAP registry tests."""

    def test_contains_expected_keys(self) -> None:
        """STRATEGY_MAP contains all three strategy names."""
        expected = {
            STRATEGY_NAME_MANUAL,
            STRATEGY_NAME_ROLE_BASED,
            STRATEGY_NAME_LOAD_BALANCED,
        }
        assert set(STRATEGY_MAP.keys()) == expected

    def test_values_are_correct_types(self) -> None:
        """Each registry value is an instance of the expected class."""
        assert isinstance(STRATEGY_MAP["manual"], ManualAssignmentStrategy)
        assert isinstance(
            STRATEGY_MAP["role_based"],
            RoleBasedAssignmentStrategy,
        )
        assert isinstance(
            STRATEGY_MAP["load_balanced"],
            LoadBalancedAssignmentStrategy,
        )

    def test_map_is_immutable(self) -> None:
        """STRATEGY_MAP is a MappingProxyType and rejects mutation."""
        with pytest.raises(TypeError):
            STRATEGY_MAP["custom"] = ManualAssignmentStrategy()  # type: ignore[index]


class TestProtocolConformance:
    """Protocol conformance tests for strategy implementations."""

    def test_manual_satisfies_protocol(self) -> None:
        assert isinstance(ManualAssignmentStrategy(), TaskAssignmentStrategy)

    def test_role_based_satisfies_protocol(self) -> None:
        scorer = AgentTaskScorer()
        assert isinstance(
            RoleBasedAssignmentStrategy(scorer),
            TaskAssignmentStrategy,
        )

    def test_load_balanced_satisfies_protocol(self) -> None:
        scorer = AgentTaskScorer()
        assert isinstance(
            LoadBalancedAssignmentStrategy(scorer),
            TaskAssignmentStrategy,
        )
