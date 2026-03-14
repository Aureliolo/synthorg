"""Unit tests for Manual, RoleBased, and LoadBalanced assignment strategies."""

import pytest

from synthorg.core.enums import AgentStatus, Complexity, SeniorityLevel
from synthorg.engine.assignment.models import (
    AgentWorkload,
    AssignmentRequest,
)
from synthorg.engine.assignment.strategies import (
    CostOptimizedAssignmentStrategy,
    LoadBalancedAssignmentStrategy,
    ManualAssignmentStrategy,
    RoleBasedAssignmentStrategy,
)
from synthorg.engine.errors import NoEligibleAgentError, TaskAssignmentError
from synthorg.engine.routing.scorer import AgentTaskScorer

from .conftest import make_assignment_agent, make_assignment_task

pytestmark = pytest.mark.unit


class TestManualAssignmentStrategy:
    """ManualAssignmentStrategy tests."""

    def test_success_with_valid_assigned_to(self) -> None:
        """Manual assignment succeeds when assigned_to matches an active agent."""
        strategy = ManualAssignmentStrategy()
        agent = make_assignment_agent("dev-1")
        task = make_assignment_task(
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
        task = make_assignment_task()
        request = AssignmentRequest(
            task=task,
            available_agents=(make_assignment_agent("dev-1"),),
        )

        with pytest.raises(TaskAssignmentError, match="assigned_to"):
            strategy.assign(request)

    def test_error_when_agent_not_in_pool(self) -> None:
        """Manual assignment raises NoEligibleAgentError when agent not found."""
        strategy = ManualAssignmentStrategy()
        agent_in_pool = make_assignment_agent("dev-1")
        task = make_assignment_task(
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
        agent = make_assignment_agent("dev-1", status=status)
        task = make_assignment_task(
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
        backend = make_assignment_agent(
            "backend",
            primary_skills=("python", "api-design"),
            level=SeniorityLevel.MID,
        )
        # Frontend dev has non-matching skills
        frontend = make_assignment_agent(
            "frontend",
            primary_skills=("typescript", "react"),
            level=SeniorityLevel.MID,
        )

        task = make_assignment_task(estimated_complexity=Complexity.MEDIUM)
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

        agent1 = make_assignment_agent(
            "dev-1",
            primary_skills=("python",),
            level=SeniorityLevel.MID,
        )
        agent2 = make_assignment_agent(
            "dev-2",
            primary_skills=("python",),
            level=SeniorityLevel.MID,
        )

        task = make_assignment_task(estimated_complexity=Complexity.MEDIUM)
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
        agent = make_assignment_agent(
            "qa",
            primary_skills=("testing",),
            level=SeniorityLevel.JUNIOR,
        )

        task = make_assignment_task(estimated_complexity=Complexity.EPIC)
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

        agent = make_assignment_agent("dev-1", level=SeniorityLevel.MID)
        task = make_assignment_task(estimated_complexity=Complexity.MEDIUM)
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

        busy = make_assignment_agent(
            "busy-dev",
            primary_skills=("python",),
            level=SeniorityLevel.MID,
        )
        idle = make_assignment_agent(
            "idle-dev",
            primary_skills=("python",),
            level=SeniorityLevel.MID,
        )

        task = make_assignment_task(estimated_complexity=Complexity.MEDIUM)
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
        better_dev = make_assignment_agent(
            "better-dev",
            primary_skills=("python", "api-design"),
            role="Backend Developer",
            level=SeniorityLevel.MID,
        )
        other_dev = make_assignment_agent(
            "other-dev",
            primary_skills=("testing",),
            level=SeniorityLevel.MID,
        )

        task = make_assignment_task(estimated_complexity=Complexity.MEDIUM)
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

        best = make_assignment_agent(
            "best-dev",
            primary_skills=("python", "api-design"),
            level=SeniorityLevel.MID,
        )
        other = make_assignment_agent(
            "other-dev",
            primary_skills=("testing",),
            level=SeniorityLevel.MID,
        )

        task = make_assignment_task(estimated_complexity=Complexity.MEDIUM)
        request = AssignmentRequest(
            task=task,
            available_agents=(best, other),
            required_skills=("python", "api-design"),
        )

        result = strategy.assign(request)

        assert result.selected is not None
        assert result.selected.agent_identity.name == "best-dev"
        assert "insufficient workload data" in result.reason

    @pytest.mark.parametrize(
        ("workloads", "expected_winner"),
        [
            ((0, 3, 5), "dev-0"),
            ((2, 2, 0), "dev-2"),
            # all equal workload; dev-0 wins by sort stability
            ((1, 1, 1), "dev-0"),
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
            make_assignment_agent(
                f"dev-{i}",
                primary_skills=("python",),
                level=SeniorityLevel.MID,
            )
            for i in range(3)
        )

        task = make_assignment_task(estimated_complexity=Complexity.MEDIUM)
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

        agent = make_assignment_agent(
            "qa",
            primary_skills=("testing",),
            level=SeniorityLevel.JUNIOR,
        )

        task = make_assignment_task(estimated_complexity=Complexity.EPIC)
        request = AssignmentRequest(
            task=task,
            available_agents=(agent,),
            required_skills=("python", "api-design"),
            required_role="Backend Developer",
            min_score=0.5,
        )

        result = strategy.assign(request)

        assert result.selected is None

    def test_partial_workload_data_falls_back(self) -> None:
        """Incomplete workload data falls back to score-based ranking."""
        scorer = AgentTaskScorer()
        strategy = LoadBalancedAssignmentStrategy(scorer)

        known = make_assignment_agent(
            "known-dev",
            primary_skills=("python",),
            level=SeniorityLevel.MID,
        )
        unknown = make_assignment_agent(
            "unknown-dev",
            primary_skills=("python",),
            level=SeniorityLevel.MID,
        )

        task = make_assignment_task(estimated_complexity=Complexity.MEDIUM)
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
        # Falls back to capability: known-dev first by sort stability
        assert result.selected.agent_identity.name == "known-dev"
        assert "insufficient workload data" in result.reason

    def test_name_property(self) -> None:
        """Strategy name is 'load_balanced'."""
        scorer = AgentTaskScorer()
        assert LoadBalancedAssignmentStrategy(scorer).name == "load_balanced"


class TestScorerBasedStrategies:
    """Shared behavior tests across all scorer-based strategies."""

    def test_inactive_agents_excluded_from_scoring(self) -> None:
        """Scorer-based strategies exclude non-ACTIVE agents."""
        scorer = AgentTaskScorer()
        strategy = CostOptimizedAssignmentStrategy(scorer)

        active = make_assignment_agent(
            "active-dev",
            primary_skills=("python",),
            level=SeniorityLevel.MID,
        )
        on_leave = make_assignment_agent(
            "leave-dev",
            primary_skills=("python", "api-design"),
            level=SeniorityLevel.SENIOR,
            status=AgentStatus.ON_LEAVE,
        )

        task = make_assignment_task(estimated_complexity=Complexity.MEDIUM)
        request = AssignmentRequest(
            task=task,
            available_agents=(active, on_leave),
            required_skills=("python",),
        )

        result = strategy.assign(request)

        assert result.selected is not None
        assert result.selected.agent_identity.name == "active-dev"
        assert all(a.agent_identity.name != "leave-dev" for a in result.alternatives)


class TestMaxConcurrentTasksEnforcement:
    """Verify max_concurrent_tasks filters out agents at capacity."""

    def test_agent_at_capacity_excluded(self) -> None:
        """Agent at max_concurrent_tasks is not selected."""
        scorer = AgentTaskScorer()
        strategy = RoleBasedAssignmentStrategy(scorer)

        busy = make_assignment_agent(
            "busy-dev",
            primary_skills=("python", "api-design"),
            role="Backend Developer",
            level=SeniorityLevel.SENIOR,
        )
        available = make_assignment_agent(
            "free-dev",
            primary_skills=("python",),
            level=SeniorityLevel.MID,
        )

        task = make_assignment_task(estimated_complexity=Complexity.MEDIUM)
        request = AssignmentRequest(
            task=task,
            available_agents=(busy, available),
            required_skills=("python",),
            max_concurrent_tasks=3,
            workloads=(
                AgentWorkload(
                    agent_id=str(busy.id),
                    active_task_count=3,
                    total_cost_usd=0.0,
                ),
                AgentWorkload(
                    agent_id=str(available.id),
                    active_task_count=1,
                    total_cost_usd=0.0,
                ),
            ),
        )

        result = strategy.assign(request)

        assert result.selected is not None
        assert result.selected.agent_identity.name == "free-dev"

    def test_no_limit_keeps_all_agents(self) -> None:
        """Without max_concurrent_tasks, all agents are eligible."""
        scorer = AgentTaskScorer()
        strategy = RoleBasedAssignmentStrategy(scorer)

        busy = make_assignment_agent(
            "busy-dev",
            primary_skills=("python", "api-design"),
            role="Backend Developer",
            level=SeniorityLevel.SENIOR,
        )
        other = make_assignment_agent(
            "other-dev",
            primary_skills=("python",),
            level=SeniorityLevel.MID,
        )

        task = make_assignment_task(estimated_complexity=Complexity.MEDIUM)
        request = AssignmentRequest(
            task=task,
            available_agents=(busy, other),
            required_skills=("python",),
            # max_concurrent_tasks is None (no limit)
            workloads=(
                AgentWorkload(
                    agent_id=str(busy.id),
                    active_task_count=99,
                    total_cost_usd=0.0,
                ),
                AgentWorkload(
                    agent_id=str(other.id),
                    active_task_count=1,
                    total_cost_usd=0.0,
                ),
            ),
        )

        result = strategy.assign(request)

        assert result.selected is not None
        # busy-dev has better matching skills so wins despite being busy
        assert result.selected.agent_identity.name == "busy-dev"

    def test_all_agents_at_capacity_returns_none(self) -> None:
        """Returns selected=None when all agents are at capacity."""
        scorer = AgentTaskScorer()
        strategy = RoleBasedAssignmentStrategy(scorer)

        dev1 = make_assignment_agent(
            "dev-1",
            primary_skills=("python",),
            level=SeniorityLevel.MID,
        )
        dev2 = make_assignment_agent(
            "dev-2",
            primary_skills=("python",),
            level=SeniorityLevel.MID,
        )

        task = make_assignment_task(estimated_complexity=Complexity.MEDIUM)
        request = AssignmentRequest(
            task=task,
            available_agents=(dev1, dev2),
            required_skills=("python",),
            max_concurrent_tasks=2,
            workloads=(
                AgentWorkload(
                    agent_id=str(dev1.id),
                    active_task_count=2,
                    total_cost_usd=0.0,
                ),
                AgentWorkload(
                    agent_id=str(dev2.id),
                    active_task_count=3,
                    total_cost_usd=0.0,
                ),
            ),
        )

        result = strategy.assign(request)

        assert result.selected is None

    def test_max_concurrent_with_empty_workloads(self) -> None:
        """max_concurrent_tasks set but no workloads → no filtering."""
        scorer = AgentTaskScorer()
        strategy = RoleBasedAssignmentStrategy(scorer)

        dev = make_assignment_agent(
            "solo-dev",
            primary_skills=("python",),
            level=SeniorityLevel.MID,
        )

        task = make_assignment_task(estimated_complexity=Complexity.MEDIUM)
        request = AssignmentRequest(
            task=task,
            available_agents=(dev,),
            required_skills=("python",),
            max_concurrent_tasks=1,
            # No workloads — capacity filter should not apply
        )

        result = strategy.assign(request)

        assert result.selected is not None
        assert result.selected.agent_identity.name == "solo-dev"
