"""Unit tests for CostOptimizedAssignmentStrategy."""

import pytest

from synthorg.core.enums import Complexity, SeniorityLevel
from synthorg.engine.assignment.models import (
    AgentWorkload,
    AssignmentRequest,
)
from synthorg.engine.assignment.strategies import (
    CostOptimizedAssignmentStrategy,
)
from synthorg.engine.routing.scorer import AgentTaskScorer

from .conftest import make_assignment_agent, make_assignment_task

pytestmark = pytest.mark.unit


class TestCostOptimizedAssignmentStrategy:
    """CostOptimizedAssignmentStrategy tests."""

    def test_cheapest_agent_selected(self) -> None:
        """Agent with lower total_cost_usd wins."""
        scorer = AgentTaskScorer()
        strategy = CostOptimizedAssignmentStrategy(scorer)

        expensive = make_assignment_agent(
            "expensive-dev",
            primary_skills=("python",),
            level=SeniorityLevel.MID,
        )
        cheap = make_assignment_agent(
            "cheap-dev",
            primary_skills=("python",),
            level=SeniorityLevel.MID,
        )

        task = make_assignment_task(estimated_complexity=Complexity.MEDIUM)
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
        assert result.strategy_used == "cost_optimized"
        assert "Cheapest:" in result.reason

    def test_cost_tie_broken_by_score(self) -> None:
        """Equal cost, higher score wins."""
        scorer = AgentTaskScorer()
        strategy = CostOptimizedAssignmentStrategy(scorer)

        better = make_assignment_agent(
            "better-dev",
            primary_skills=("python", "api-design"),
            role="Backend Developer",
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
        assert "insufficient cost data" in result.reason

    def test_no_eligible_returns_none(self) -> None:
        """All below min_score returns selected=None."""
        scorer = AgentTaskScorer()
        strategy = CostOptimizedAssignmentStrategy(scorer)

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

    def test_partial_cost_data(self) -> None:
        """Incomplete cost data triggers score-only fallback."""
        scorer = AgentTaskScorer()
        strategy = CostOptimizedAssignmentStrategy(scorer)

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
                    active_task_count=1,
                    total_cost_usd=30.0,
                ),
            ),
        )

        result = strategy.assign(request)

        assert result.selected is not None
        # Incomplete cost data → score-only fallback; equal scores,
        # known-dev wins by sort stability (first in available_agents).
        assert result.selected.agent_identity.name == "known-dev"
        assert "insufficient cost data" in result.reason

    @pytest.mark.parametrize(
        ("costs", "expected_winner"),
        [
            ((10.0, 30.0, 50.0), "dev-0"),
            ((50.0, 50.0, 5.0), "dev-2"),
            # all equal cost; dev-0 wins by sort stability
            ((20.0, 20.0, 20.0), "dev-0"),
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
