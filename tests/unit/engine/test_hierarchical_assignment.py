"""Unit tests for HierarchicalAssignmentStrategy."""

import pytest

from synthorg.communication.delegation.hierarchy import HierarchyResolver
from synthorg.core.company import Company, Department, Team
from synthorg.core.enums import Complexity, SeniorityLevel
from synthorg.engine.assignment.models import AssignmentRequest
from synthorg.engine.assignment.strategies import (
    HierarchicalAssignmentStrategy,
)
from synthorg.engine.routing.scorer import AgentTaskScorer

from .conftest import make_assignment_agent, make_assignment_task

pytestmark = pytest.mark.unit


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

        dev1 = make_assignment_agent(
            "dev-1",
            primary_skills=("python",),
            level=SeniorityLevel.MID,
        )

        task = make_assignment_task(
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
        assert result.strategy_used == "hierarchical"
        assert "Delegated from" in result.reason

    def test_best_scoring_direct_report_wins(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        """Multiple reports, highest score wins."""
        scorer = AgentTaskScorer()
        strategy = HierarchicalAssignmentStrategy(scorer, hierarchy)

        dev1 = make_assignment_agent(
            "dev-1",
            primary_skills=("python", "api-design"),
            level=SeniorityLevel.MID,
        )
        dev2 = make_assignment_agent(
            "dev-2",
            primary_skills=("testing",),
            level=SeniorityLevel.JUNIOR,
        )

        task = make_assignment_task(
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
        dev1 = make_assignment_agent(
            "dev-1",
            primary_skills=("python",),
            level=SeniorityLevel.MID,
        )

        task = make_assignment_task(
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

        dev1 = make_assignment_agent(
            "dev-1",
            primary_skills=("python",),
            level=SeniorityLevel.MID,
        )

        # created_by is "manager" but delegation_chain[-1] is "lead"
        task = make_assignment_task(
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

    def test_single_element_delegation_chain(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        """Single-element delegation_chain resolves correctly."""
        scorer = AgentTaskScorer()
        strategy = HierarchicalAssignmentStrategy(scorer, hierarchy)

        dev1 = make_assignment_agent(
            "dev-1",
            primary_skills=("python",),
            level=SeniorityLevel.MID,
        )

        task = make_assignment_task(
            created_by="manager",
            delegation_chain=("lead",),
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
        other = make_assignment_agent(
            "outsider",
            primary_skills=("python",),
            level=SeniorityLevel.MID,
        )

        task = make_assignment_task(
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

        dev1 = make_assignment_agent(
            "dev-1",
            primary_skills=("python",),
            level=SeniorityLevel.MID,
        )

        task = make_assignment_task(
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

    def test_reports_in_hierarchy_but_not_in_pool(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        """Delegator has reports but none are in available_agents."""
        scorer = AgentTaskScorer()
        strategy = HierarchicalAssignmentStrategy(scorer, hierarchy)

        # lead has dev-1 and dev-2 as reports, but neither is
        # in the pool — only an unrelated agent is.
        outsider = make_assignment_agent(
            "outsider",
            primary_skills=("python",),
            level=SeniorityLevel.MID,
        )

        task = make_assignment_task(
            created_by="lead",
            estimated_complexity=Complexity.MEDIUM,
        )
        request = AssignmentRequest(
            task=task,
            available_agents=(outsider,),
            required_skills=("python",),
        )

        result = strategy.assign(request)

        assert result.selected is None
        assert "No subordinates" in result.reason

    def test_subordinates_below_min_score(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        """Subordinates exist but all score below min_score."""
        scorer = AgentTaskScorer()
        strategy = HierarchicalAssignmentStrategy(scorer, hierarchy)

        # dev-1 is a direct report of lead but has mismatched skills
        dev1 = make_assignment_agent(
            "dev-1",
            primary_skills=("testing",),
            level=SeniorityLevel.JUNIOR,
        )

        task = make_assignment_task(
            created_by="lead",
            estimated_complexity=Complexity.EPIC,
        )
        request = AssignmentRequest(
            task=task,
            available_agents=(dev1,),
            required_skills=("python", "api-design", "databases"),
            required_role="Backend Developer",
            min_score=0.9,
        )

        result = strategy.assign(request)

        assert result.selected is None
        assert "scored above threshold" in result.reason

    def test_name_property(
        self,
        hierarchy: HierarchyResolver,
    ) -> None:
        """Strategy name is 'hierarchical'."""
        scorer = AgentTaskScorer()
        assert HierarchicalAssignmentStrategy(scorer, hierarchy).name == "hierarchical"
