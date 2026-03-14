"""Unit tests for AuctionAssignmentStrategy."""

import pytest

from synthorg.core.enums import Complexity, SeniorityLevel
from synthorg.engine.assignment.models import (
    AgentWorkload,
    AssignmentRequest,
)
from synthorg.engine.assignment.strategies import (
    AuctionAssignmentStrategy,
)
from synthorg.engine.routing.scorer import AgentTaskScorer

from .conftest import make_assignment_agent, make_assignment_task

pytestmark = pytest.mark.unit


class TestAuctionAssignmentStrategy:
    """AuctionAssignmentStrategy tests."""

    def test_highest_bid_wins(self) -> None:
        """Best combined score+availability wins."""
        scorer = AgentTaskScorer()
        strategy = AuctionAssignmentStrategy(scorer)

        agent_a = make_assignment_agent(
            "agent-a",
            primary_skills=("python", "api-design"),
            level=SeniorityLevel.SENIOR,
        )
        agent_b = make_assignment_agent(
            "agent-b",
            primary_skills=("python",),
            level=SeniorityLevel.MID,
        )

        task = make_assignment_task(estimated_complexity=Complexity.MEDIUM)
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
        assert result.strategy_used == "auction"
        assert "Auction winner:" in result.reason
        assert len(result.alternatives) == 1
        assert result.alternatives[0].agent_identity.name == "agent-b"

    def test_idle_agent_preferred_over_busy(self) -> None:
        """Equal scores, idle agent wins."""
        scorer = AgentTaskScorer()
        strategy = AuctionAssignmentStrategy(scorer)

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
        expert = make_assignment_agent(
            "expert",
            primary_skills=("python", "api-design", "databases"),
            role="Backend Developer",
            level=SeniorityLevel.SENIOR,
        )
        # Novice with low score but idle
        novice = make_assignment_agent(
            "novice",
            primary_skills=("testing",),
            level=SeniorityLevel.JUNIOR,
        )

        task = make_assignment_task(estimated_complexity=Complexity.MEDIUM)
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

    def test_no_eligible_returns_none(self) -> None:
        """All below min_score returns selected=None."""
        scorer = AgentTaskScorer()
        strategy = AuctionAssignmentStrategy(scorer)

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

    @pytest.mark.parametrize(
        ("task_counts", "expected_winner"),
        [
            # dev-0 and dev-1 both idle with equal scores;
            # dev-0 wins by sort stability
            ((0, 0, 5), "dev-0"),
            ((3, 0, 3), "dev-1"),  # dev-1 idle wins
            ((0, 0, 0), "dev-0"),  # all idle, first by stability
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
