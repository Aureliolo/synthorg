"""Strategy registry and factory for task assignment.

``STRATEGY_MAP`` provides all pre-built strategies except
``HierarchicalAssignmentStrategy`` as an immutable mapping.
``build_strategy_map`` is the preferred factory when a
``HierarchyResolver`` is available (adds the hierarchical
strategy) or a custom ``AgentTaskScorer`` is needed.
"""

from types import MappingProxyType
from typing import TYPE_CHECKING

from synthorg.engine.assignment.strategies import (
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
from synthorg.engine.routing.scorer import AgentTaskScorer
from synthorg.observability import get_logger

if TYPE_CHECKING:
    from synthorg.communication.delegation.hierarchy import (
        HierarchyResolver,
    )
    from synthorg.engine.assignment.protocol import (
        TaskAssignmentStrategy,
    )

logger = get_logger(__name__)

_DEFAULT_SCORER = AgentTaskScorer()

# Excludes HierarchicalAssignmentStrategy — it requires a
# HierarchyResolver at construction.  Use
# build_strategy_map(hierarchy=...) to get a complete map
# that includes all strategies.
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
    Otherwise, returns the same strategies as the static
    ``STRATEGY_MAP``.

    Args:
        hierarchy: Optional hierarchy resolver for the
            hierarchical strategy.
        scorer: Optional custom scorer.  Defaults to the
            shared module-level ``AgentTaskScorer`` instance.

    Returns:
        Immutable mapping of strategy names to instances.
    """
    effective_scorer = scorer if scorer is not None else _DEFAULT_SCORER

    logger.debug(
        "task_assignment.registry.build",
        has_hierarchy=hierarchy is not None,
        custom_scorer=scorer is not None,
    )

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
