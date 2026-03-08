"""Unit tests for task assignment strategies."""

from datetime import date
from types import MappingProxyType

import pytest

from ai_company.communication.delegation.hierarchy import HierarchyResolver
from ai_company.core.agent import AgentIdentity, ModelConfig, SkillSet
from ai_company.core.company import Company, Department, Team
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
    STRATEGY_NAME_AUCTION,
    STRATEGY_NAME_COST_OPTIMIZED,
    STRATEGY_NAME_HIERARCHICAL,
    STRATEGY_NAME_LOAD_BALANCED,
    STRATEGY_NAME_MANUAL,
    STRATEGY_NAME_ROLE_BASED,
    AuctionAssignmentStrategy,
    CostOptimizedAssignmentStrategy,
    HierarchicalAssignmentStrategy,
    LoadBalancedAssignmentStrategy,
    ManualAssignmentStrategy,
    RoleBasedAssignmentStrategy,
    build_strategy_map,
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


class TestCostOptimizedAssignmentStrategy:
    """CostOptimizedAssignmentStrategy tests."""

    def test_cheapest_agent_selected(self) -> None:
        """Agent with lower total_cost_usd wins."""
        scorer = AgentTaskScorer()
        strategy = CostOptimizedAssignmentStrategy(scorer)

        expensive = _make_agent(
            "expensive-dev",
            primary_skills=("python",),
            level=SeniorityLevel.MID,
        )
        cheap = _make_agent(
            "cheap-dev",
            primary_skills=("python",),
            level=SeniorityLevel.MID,
        )

        task = _make_task(estimated_complexity=Complexity.MEDIUM)
        request = AssignmentRequest(
            task=task,
            available_agents=(expensive, cheap),
            required_skills=("python",),
            workloads=(
                AgentWorkload(
                    agent_id=str(expensive.id),
                    active_task_count=1,
                    total_cost_usd=50.0,
                ),
                AgentWorkload(
                    agent_id=str(cheap.id),
                    active_task_count=1,
                    total_cost_usd=10.0,
                ),
            ),
        )

        result = strategy.assign(request)

        assert result.selected is not None
        assert result.selected.agent_identity.name == "cheap-dev"

    def test_cost_tie_broken_by_score(self) -> None:
        """Equal cost, higher score wins."""
        scorer = AgentTaskScorer()
        strategy = CostOptimizedAssignmentStrategy(scorer)

        better = _make_agent(
            "better-dev",
            primary_skills=("python", "api-design"),
            role="Backend Developer",
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
            available_agents=(better, other),
            required_skills=("python", "api-design"),
            required_role="Backend Developer",
            workloads=(
                AgentWorkload(
                    agent_id=str(better.id),
                    active_task_count=1,
                    total_cost_usd=20.0,
                ),
                AgentWorkload(
                    agent_id=str(other.id),
                    active_task_count=1,
                    total_cost_usd=20.0,
                ),
            ),
        )

        result = strategy.assign(request)

        assert result.selected is not None
        assert result.selected.agent_identity.name == "better-dev"

    def test_empty_workloads_falls_back_to_capability(self) -> None:
        """Without workloads, falls back to score-only sorting."""
        scorer = AgentTaskScorer()
        strategy = CostOptimizedAssignmentStrategy(scorer)

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

    def test_no_eligible_returns_none(self) -> None:
        """All below min_score returns selected=None."""
        scorer = AgentTaskScorer()
        strategy = CostOptimizedAssignmentStrategy(scorer)

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

    def test_partial_cost_data(self) -> None:
        """Missing agents default to 0.0 cost."""
        scorer = AgentTaskScorer()
        strategy = CostOptimizedAssignmentStrategy(scorer)

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
                    active_task_count=1,
                    total_cost_usd=30.0,
                ),
            ),
        )

        result = strategy.assign(request)

        assert result.selected is not None
        # unknown-dev defaults to 0.0 cost, so it wins
        assert result.selected.agent_identity.name == "unknown-dev"

    @pytest.mark.parametrize(
        ("costs", "expected_winner"),
        [
            ((10.0, 30.0, 50.0), "dev-0"),
            ((50.0, 50.0, 5.0), "dev-2"),
            ((20.0, 20.0, 20.0), "dev-0"),  # all equal, highest score wins
        ],
        ids=["first-cheapest", "last-cheapest", "all-equal"],
    )
    def test_parametrized_cost_distributions(
        self,
        costs: tuple[float, ...],
        expected_winner: str,
    ) -> None:
        """Parametrized test for various cost distributions."""
        scorer = AgentTaskScorer()
        strategy = CostOptimizedAssignmentStrategy(scorer)

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
                    active_task_count=1,
                    total_cost_usd=c,
                )
                for i, c in enumerate(costs)
            ),
        )

        result = strategy.assign(request)

        assert result.selected is not None
        assert result.selected.agent_identity.name == expected_winner

    def test_name_property(self) -> None:
        """Strategy name is 'cost_optimized'."""
        scorer = AgentTaskScorer()
        assert CostOptimizedAssignmentStrategy(scorer).name == "cost_optimized"


class TestHierarchicalAssignmentStrategy:
    """HierarchicalAssignmentStrategy tests."""

    @pytest.fixture
    def hierarchy(self) -> HierarchyResolver:
        """Build a minimal hierarchy: manager -> lead -> dev-1, dev-2."""
        company = Company(
            name="Test Corp",
            departments=(
                Department(
                    name="Engineering",
                    head="manager",
                    teams=(
                        Team(
                            name="platform",
                            lead="lead",
                            members=("dev-1", "dev-2"),
                        ),
                    ),
                ),
            ),
        )
        return HierarchyResolver(company)

    def test_direct_report_selected(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        """Delegator's direct report in pool is selected."""
        scorer = AgentTaskScorer()
        strategy = HierarchicalAssignmentStrategy(scorer, hierarchy)

        dev1 = _make_agent(
            "dev-1",
            primary_skills=("python",),
            level=SeniorityLevel.MID,
        )

        task = _make_task(
            created_by="lead",
            estimated_complexity=Complexity.MEDIUM,
        )
        request = AssignmentRequest(
            task=task,
            available_agents=(dev1,),
            required_skills=("python",),
        )

        result = strategy.assign(request)

        assert result.selected is not None
        assert result.selected.agent_identity.name == "dev-1"

    def test_best_scoring_direct_report_wins(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        """Multiple reports, highest score wins."""
        scorer = AgentTaskScorer()
        strategy = HierarchicalAssignmentStrategy(scorer, hierarchy)

        dev1 = _make_agent(
            "dev-1",
            primary_skills=("python", "api-design"),
            level=SeniorityLevel.MID,
        )
        dev2 = _make_agent(
            "dev-2",
            primary_skills=("testing",),
            level=SeniorityLevel.JUNIOR,
        )

        task = _make_task(
            created_by="lead",
            estimated_complexity=Complexity.MEDIUM,
        )
        request = AssignmentRequest(
            task=task,
            available_agents=(dev1, dev2),
            required_skills=("python", "api-design"),
        )

        result = strategy.assign(request)

        assert result.selected is not None
        assert result.selected.agent_identity.name == "dev-1"

    def test_fallback_to_subordinate(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        """No direct report match -> finds transitive subordinate."""
        scorer = AgentTaskScorer()
        strategy = HierarchicalAssignmentStrategy(scorer, hierarchy)

        # manager -> lead -> dev-1; dev-1 is a transitive subordinate
        dev1 = _make_agent(
            "dev-1",
            primary_skills=("python",),
            level=SeniorityLevel.MID,
        )

        task = _make_task(
            created_by="manager",
            estimated_complexity=Complexity.MEDIUM,
        )
        request = AssignmentRequest(
            task=task,
            available_agents=(dev1,),
            required_skills=("python",),
        )

        result = strategy.assign(request)

        assert result.selected is not None
        assert result.selected.agent_identity.name == "dev-1"

    def test_delegation_chain_used_over_created_by(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        """delegation_chain[-1] takes precedence over created_by."""
        scorer = AgentTaskScorer()
        strategy = HierarchicalAssignmentStrategy(scorer, hierarchy)

        dev1 = _make_agent(
            "dev-1",
            primary_skills=("python",),
            level=SeniorityLevel.MID,
        )

        # created_by is "manager" but delegation_chain[-1] is "lead"
        task = _make_task(
            created_by="manager",
            delegation_chain=("manager", "lead"),
            estimated_complexity=Complexity.MEDIUM,
        )
        request = AssignmentRequest(
            task=task,
            available_agents=(dev1,),
            required_skills=("python",),
        )

        result = strategy.assign(request)

        assert result.selected is not None
        assert result.selected.agent_identity.name == "dev-1"

    def test_no_subordinates_returns_none(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        """Delegator has no reports -> selected=None."""
        scorer = AgentTaskScorer()
        strategy = HierarchicalAssignmentStrategy(scorer, hierarchy)

        # dev-1 has no reports; only unrelated agent in pool
        other = _make_agent(
            "outsider",
            primary_skills=("python",),
            level=SeniorityLevel.MID,
        )

        task = _make_task(
            created_by="dev-1",
            estimated_complexity=Complexity.MEDIUM,
        )
        request = AssignmentRequest(
            task=task,
            available_agents=(other,),
            required_skills=("python",),
        )

        result = strategy.assign(request)

        assert result.selected is None

    def test_unknown_delegator_returns_none(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        """Delegator not in hierarchy -> selected=None."""
        scorer = AgentTaskScorer()
        strategy = HierarchicalAssignmentStrategy(scorer, hierarchy)

        dev1 = _make_agent(
            "dev-1",
            primary_skills=("python",),
            level=SeniorityLevel.MID,
        )

        task = _make_task(
            created_by="unknown-person",
            estimated_complexity=Complexity.MEDIUM,
        )
        request = AssignmentRequest(
            task=task,
            available_agents=(dev1,),
            required_skills=("python",),
        )

        result = strategy.assign(request)

        assert result.selected is None

    def test_name_property(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        """Strategy name is 'hierarchical'."""
        scorer = AgentTaskScorer()
        assert HierarchicalAssignmentStrategy(scorer, hierarchy).name == "hierarchical"


class TestAuctionAssignmentStrategy:
    """AuctionAssignmentStrategy tests."""

    def test_highest_bid_wins(self) -> None:
        """Best combined score+availability wins."""
        scorer = AgentTaskScorer()
        strategy = AuctionAssignmentStrategy(scorer)

        agent_a = _make_agent(
            "agent-a",
            primary_skills=("python", "api-design"),
            level=SeniorityLevel.SENIOR,
        )
        agent_b = _make_agent(
            "agent-b",
            primary_skills=("python",),
            level=SeniorityLevel.MID,
        )

        task = _make_task(estimated_complexity=Complexity.MEDIUM)
        request = AssignmentRequest(
            task=task,
            available_agents=(agent_a, agent_b),
            required_skills=("python", "api-design"),
            workloads=(
                AgentWorkload(
                    agent_id=str(agent_a.id),
                    active_task_count=0,
                ),
                AgentWorkload(
                    agent_id=str(agent_b.id),
                    active_task_count=0,
                ),
            ),
        )

        result = strategy.assign(request)

        assert result.selected is not None
        # agent-a should win with higher score and same availability
        assert result.selected.agent_identity.name == "agent-a"

    def test_idle_agent_preferred_over_busy(self) -> None:
        """Equal scores, idle agent wins."""
        scorer = AgentTaskScorer()
        strategy = AuctionAssignmentStrategy(scorer)

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
                    active_task_count=0,
                ),
            ),
        )

        result = strategy.assign(request)

        assert result.selected is not None
        assert result.selected.agent_identity.name == "idle-dev"

    def test_high_score_can_overcome_load(self) -> None:
        """High score beats low-score idle agent."""
        scorer = AgentTaskScorer()
        strategy = AuctionAssignmentStrategy(scorer)

        # Expert with high score but some load
        expert = _make_agent(
            "expert",
            primary_skills=("python", "api-design", "databases"),
            role="Backend Developer",
            level=SeniorityLevel.SENIOR,
        )
        # Novice with low score but idle
        novice = _make_agent(
            "novice",
            primary_skills=("testing",),
            level=SeniorityLevel.JUNIOR,
        )

        task = _make_task(estimated_complexity=Complexity.MEDIUM)
        request = AssignmentRequest(
            task=task,
            available_agents=(expert, novice),
            required_skills=("python", "api-design", "databases"),
            required_role="Backend Developer",
            workloads=(
                AgentWorkload(
                    agent_id=str(expert.id),
                    active_task_count=1,
                ),
                AgentWorkload(
                    agent_id=str(novice.id),
                    active_task_count=0,
                ),
            ),
        )

        result = strategy.assign(request)

        assert result.selected is not None
        # Expert's high score should overcome the small load penalty
        assert result.selected.agent_identity.name == "expert"

    def test_empty_workloads_equivalent_to_role_based(self) -> None:
        """No workloads -> all availability=1.0, bid=score."""
        scorer = AgentTaskScorer()
        strategy = AuctionAssignmentStrategy(scorer)

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

    def test_no_eligible_returns_none(self) -> None:
        """All below min_score returns selected=None."""
        scorer = AgentTaskScorer()
        strategy = AuctionAssignmentStrategy(scorer)

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

    @pytest.mark.parametrize(
        ("task_counts", "expected_winner"),
        [
            ((0, 0, 5), "dev-0"),  # all equal score, dev-0/dev-1 idle
            ((3, 0, 3), "dev-1"),  # dev-1 idle wins
            ((0, 0, 0), "dev-0"),  # all idle, first by score
        ],
        ids=["last-busy", "middle-idle", "all-idle"],
    )
    def test_parametrized_bid_scenarios(
        self,
        task_counts: tuple[int, ...],
        expected_winner: str,
    ) -> None:
        """Various (score, load) combinations."""
        scorer = AgentTaskScorer()
        strategy = AuctionAssignmentStrategy(scorer)

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
                    active_task_count=tc,
                )
                for i, tc in enumerate(task_counts)
            ),
        )

        result = strategy.assign(request)

        assert result.selected is not None
        assert result.selected.agent_identity.name == expected_winner

    def test_name_property(self) -> None:
        """Strategy name is 'auction'."""
        scorer = AgentTaskScorer()
        assert AuctionAssignmentStrategy(scorer).name == "auction"


class TestStrategyMap:
    """STRATEGY_MAP registry tests."""

    def test_contains_expected_keys(self) -> None:
        """STRATEGY_MAP contains all five static strategy names."""
        expected = {
            STRATEGY_NAME_MANUAL,
            STRATEGY_NAME_ROLE_BASED,
            STRATEGY_NAME_LOAD_BALANCED,
            STRATEGY_NAME_COST_OPTIMIZED,
            STRATEGY_NAME_AUCTION,
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
        assert isinstance(
            STRATEGY_MAP["cost_optimized"],
            CostOptimizedAssignmentStrategy,
        )
        assert isinstance(
            STRATEGY_MAP["auction"],
            AuctionAssignmentStrategy,
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

    def test_cost_optimized_satisfies_protocol(self) -> None:
        scorer = AgentTaskScorer()
        assert isinstance(
            CostOptimizedAssignmentStrategy(scorer),
            TaskAssignmentStrategy,
        )

    def test_hierarchical_satisfies_protocol(self) -> None:
        scorer = AgentTaskScorer()
        company = Company(
            name="Test Corp",
            departments=(
                Department(
                    name="Engineering",
                    head="manager",
                    teams=(Team(name="platform", lead="lead", members=("dev-1",)),),
                ),
            ),
        )
        hierarchy = HierarchyResolver(company)
        assert isinstance(
            HierarchicalAssignmentStrategy(scorer, hierarchy),
            TaskAssignmentStrategy,
        )

    def test_auction_satisfies_protocol(self) -> None:
        scorer = AgentTaskScorer()
        assert isinstance(
            AuctionAssignmentStrategy(scorer),
            TaskAssignmentStrategy,
        )


class TestBuildStrategyMap:
    """build_strategy_map factory tests."""

    def test_without_hierarchy_excludes_hierarchical(self) -> None:
        """Returns 5 strategies when hierarchy is None."""
        result = build_strategy_map()

        assert len(result) == 5
        assert STRATEGY_NAME_HIERARCHICAL not in result

    def test_with_hierarchy_includes_hierarchical(self) -> None:
        """Returns all 6 strategies when hierarchy is provided."""
        company = Company(
            name="Test Corp",
            departments=(
                Department(
                    name="Engineering",
                    head="manager",
                    teams=(Team(name="platform", lead="lead", members=("dev-1",)),),
                ),
            ),
        )
        hierarchy = HierarchyResolver(company)

        result = build_strategy_map(hierarchy=hierarchy)

        assert len(result) == 6
        assert STRATEGY_NAME_HIERARCHICAL in result
        assert isinstance(
            result[STRATEGY_NAME_HIERARCHICAL],
            HierarchicalAssignmentStrategy,
        )

    def test_returns_mapping_proxy(self) -> None:
        """Result is MappingProxyType."""
        result = build_strategy_map()

        assert isinstance(result, MappingProxyType)
