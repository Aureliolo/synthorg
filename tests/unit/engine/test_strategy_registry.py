"""Unit tests for strategy registry, protocol conformance, and factory."""

from types import MappingProxyType

import pytest

from ai_company.communication.delegation.hierarchy import HierarchyResolver
from ai_company.core.company import Company, Department, Team
from ai_company.core.enums import Complexity, SeniorityLevel
from ai_company.engine.assignment.models import AssignmentRequest
from ai_company.engine.assignment.protocol import TaskAssignmentStrategy
from ai_company.engine.assignment.registry import (
    STRATEGY_MAP,
    build_strategy_map,
)
from ai_company.engine.assignment.strategies import (
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
)
from ai_company.engine.routing.scorer import AgentTaskScorer

from .conftest import make_assignment_agent, make_assignment_task

pytestmark = pytest.mark.unit


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

    def test_excludes_hierarchical(self) -> None:
        """STRATEGY_MAP does not include hierarchical (requires runtime dep)."""
        assert STRATEGY_NAME_HIERARCHICAL not in STRATEGY_MAP

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
                    teams=(
                        Team(
                            name="platform",
                            lead="lead",
                            members=("dev-1",),
                        ),
                    ),
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
                    teams=(
                        Team(
                            name="platform",
                            lead="lead",
                            members=("dev-1",),
                        ),
                    ),
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

    def test_custom_scorer_injected(self) -> None:
        """Custom scorer is used by strategies in the returned map."""
        custom_scorer = AgentTaskScorer(min_score=0.5)
        result = build_strategy_map(scorer=custom_scorer)

        # Verify the custom scorer is effective by running an
        # assignment that would pass default min_score (0.1) but
        # fail with the custom min_score (0.5)
        strategy = result[STRATEGY_NAME_ROLE_BASED]
        agent = make_assignment_agent(
            "dev-1",
            primary_skills=("testing",),
            level=SeniorityLevel.JUNIOR,
        )
        task = make_assignment_task(
            estimated_complexity=Complexity.EPIC,
        )
        request = AssignmentRequest(
            task=task,
            available_agents=(agent,),
            required_skills=("python", "api-design"),
            required_role="Backend Developer",
            min_score=0.5,
        )

        assignment_result = strategy.assign(request)

        # Agent should not be selected with the high threshold
        assert assignment_result.selected is None
