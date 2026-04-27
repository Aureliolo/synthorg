"""Strategy registry and factory for task assignment.

``STRATEGY_MAP`` provides all pre-built strategies except the
hierarchical composition (which requires a ``HierarchyResolver``)
as an immutable mapping. ``build_strategy_map`` is the preferred
factory when a ``HierarchyResolver`` is available (adds the
hierarchical composition) or a custom ``AgentTaskScorer`` is
needed.
"""

from types import MappingProxyType
from typing import TYPE_CHECKING

from synthorg.engine.assignment._shared import (
    STRATEGY_NAME_AUCTION,
    STRATEGY_NAME_COST_OPTIMIZED,
    STRATEGY_NAME_HIERARCHICAL,
    STRATEGY_NAME_LOAD_BALANCED,
    STRATEGY_NAME_MANUAL,
    STRATEGY_NAME_ROLE_BASED,
)
from synthorg.engine.assignment.manual import ManualAssignmentStrategy
from synthorg.engine.assignment.pool_filters import (
    HierarchicalPoolFilter,
    IdentityPoolFilter,
)
from synthorg.engine.assignment.rankers import (
    AuctionBidRanker,
    CostDescendingRanker,
    ScoreDescendingRanker,
    WorkloadAscendingRanker,
)
from synthorg.engine.assignment.scoring_based import ScoringBasedAssignmentStrategy
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

# Excludes the hierarchical composition -- it requires a
# HierarchyResolver at construction. Use
# build_strategy_map(hierarchy=...) to get a complete map
# that includes all strategies.
STRATEGY_MAP: MappingProxyType[str, TaskAssignmentStrategy] = MappingProxyType(
    {
        STRATEGY_NAME_MANUAL: ManualAssignmentStrategy(),
        STRATEGY_NAME_ROLE_BASED: ScoringBasedAssignmentStrategy(
            name=STRATEGY_NAME_ROLE_BASED,
            scorer=_DEFAULT_SCORER,
            pool_filter=IdentityPoolFilter(),
            ranker=ScoreDescendingRanker(),
        ),
        STRATEGY_NAME_LOAD_BALANCED: ScoringBasedAssignmentStrategy(
            name=STRATEGY_NAME_LOAD_BALANCED,
            scorer=_DEFAULT_SCORER,
            pool_filter=IdentityPoolFilter(),
            ranker=WorkloadAscendingRanker(),
        ),
        STRATEGY_NAME_COST_OPTIMIZED: ScoringBasedAssignmentStrategy(
            name=STRATEGY_NAME_COST_OPTIMIZED,
            scorer=_DEFAULT_SCORER,
            pool_filter=IdentityPoolFilter(),
            ranker=CostDescendingRanker(),
        ),
        STRATEGY_NAME_AUCTION: ScoringBasedAssignmentStrategy(
            name=STRATEGY_NAME_AUCTION,
            scorer=_DEFAULT_SCORER,
            pool_filter=IdentityPoolFilter(),
            ranker=AuctionBidRanker(),
        ),
    },
)


def build_strategy_map(
    *,
    hierarchy: HierarchyResolver | None = None,
    scorer: AgentTaskScorer | None = None,
) -> MappingProxyType[str, TaskAssignmentStrategy]:
    """Build a strategy map, optionally including hierarchical.

    When ``hierarchy`` is provided, includes the hierarchical
    composition in the returned map. Otherwise, returns the same
    strategies as the static ``STRATEGY_MAP``.

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
        STRATEGY_NAME_ROLE_BASED: ScoringBasedAssignmentStrategy(
            name=STRATEGY_NAME_ROLE_BASED,
            scorer=effective_scorer,
            pool_filter=IdentityPoolFilter(),
            ranker=ScoreDescendingRanker(),
        ),
        STRATEGY_NAME_LOAD_BALANCED: ScoringBasedAssignmentStrategy(
            name=STRATEGY_NAME_LOAD_BALANCED,
            scorer=effective_scorer,
            pool_filter=IdentityPoolFilter(),
            ranker=WorkloadAscendingRanker(),
        ),
        STRATEGY_NAME_COST_OPTIMIZED: ScoringBasedAssignmentStrategy(
            name=STRATEGY_NAME_COST_OPTIMIZED,
            scorer=effective_scorer,
            pool_filter=IdentityPoolFilter(),
            ranker=CostDescendingRanker(),
        ),
        STRATEGY_NAME_AUCTION: ScoringBasedAssignmentStrategy(
            name=STRATEGY_NAME_AUCTION,
            scorer=effective_scorer,
            pool_filter=IdentityPoolFilter(),
            ranker=AuctionBidRanker(),
        ),
    }

    if hierarchy is not None:
        strategies[STRATEGY_NAME_HIERARCHICAL] = ScoringBasedAssignmentStrategy(
            name=STRATEGY_NAME_HIERARCHICAL,
            scorer=effective_scorer,
            pool_filter=HierarchicalPoolFilter(hierarchy),
            ranker=ScoreDescendingRanker(),
        )

    return MappingProxyType(strategies)
