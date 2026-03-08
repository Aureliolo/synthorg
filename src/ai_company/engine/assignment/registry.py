"""Strategy registry and factory for task assignment.

``STRATEGY_MAP`` provides five pre-built strategies as an
immutable mapping.  ``build_strategy_map`` is the preferred
factory when a ``HierarchyResolver`` is available (adds the
sixth strategy) or a custom ``AgentTaskScorer`` is needed.
"""

from types import MappingProxyType
from typing import TYPE_CHECKING

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

if TYPE_CHECKING:
    from ai_company.communication.delegation.hierarchy import (
        HierarchyResolver,
    )
    from ai_company.engine.assignment.protocol import (
        TaskAssignmentStrategy,
    )

_DEFAULT_SCORER = AgentTaskScorer()

# Excludes HierarchicalAssignmentStrategy — it requires a
# HierarchyResolver at construction.  Use
# build_strategy_map(hierarchy=...) to get a complete map
# with all six strategies.
STRATEGY_MAP: MappingProxyType[str, TaskAssignmentStrategy] = MappingProxyType(
    {
        STRATEGY_NAME_MANUAL: ManualAssignmentStrategy(),
        STRATEGY_NAME_ROLE_BASED: RoleBasedAssignmentStrategy(
            _DEFAULT_SCORER,
        ),
        STRATEGY_NAME_LOAD_BALANCED: LoadBalancedAssignmentStrategy(
            _DEFAULT_SCORER,
        ),
        STRATEGY_NAME_COST_OPTIMIZED: CostOptimizedAssignmentStrategy(
            _DEFAULT_SCORER,
        ),
        STRATEGY_NAME_AUCTION: AuctionAssignmentStrategy(
            _DEFAULT_SCORER,
        ),
    },
)


def build_strategy_map(
    *,
    hierarchy: HierarchyResolver | None = None,
    scorer: AgentTaskScorer | None = None,
) -> MappingProxyType[str, TaskAssignmentStrategy]:
    """Build a strategy map, optionally including hierarchical.

    When ``hierarchy`` is provided, includes the
    ``HierarchicalAssignmentStrategy`` in the returned map.
    Otherwise, returns the same five strategies as the static
    ``STRATEGY_MAP``.

    Args:
        hierarchy: Optional hierarchy resolver for the
            hierarchical strategy.
        scorer: Optional custom scorer.  Defaults to a new
            ``AgentTaskScorer``.

    Returns:
        Immutable mapping of strategy names to instances.
    """
    effective_scorer = scorer if scorer is not None else AgentTaskScorer()

    strategies: dict[str, TaskAssignmentStrategy] = {
        STRATEGY_NAME_MANUAL: ManualAssignmentStrategy(),
        STRATEGY_NAME_ROLE_BASED: RoleBasedAssignmentStrategy(
            effective_scorer,
        ),
        STRATEGY_NAME_LOAD_BALANCED: LoadBalancedAssignmentStrategy(
            effective_scorer,
        ),
        STRATEGY_NAME_COST_OPTIMIZED: CostOptimizedAssignmentStrategy(
            effective_scorer,
        ),
        STRATEGY_NAME_AUCTION: AuctionAssignmentStrategy(
            effective_scorer,
        ),
    }

    if hierarchy is not None:
        strategies[STRATEGY_NAME_HIERARCHICAL] = HierarchicalAssignmentStrategy(
            effective_scorer,
            hierarchy,
        )

    return MappingProxyType(strategies)
