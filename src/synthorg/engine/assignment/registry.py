"""Strategy registry and factory for task assignment.

``STRATEGY_MAP`` provides all pre-built strategies except the
hierarchical composition (which requires a ``HierarchyResolver``)
as an immutable mapping. ``build_strategy_map`` is the preferred
factory when a ``HierarchyResolver`` is available (adds the
hierarchical composition) or a custom ``AgentTaskScorer`` is
needed.
"""

from collections.abc import Callable
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
from synthorg.engine.assignment.protocol import (
    TaskAssignmentStrategy,
)
from synthorg.engine.assignment.ranker_protocol import CandidateRanker
from synthorg.engine.assignment.rankers import (
    AuctionBidRanker,
    CostDescendingRanker,
    ScoreDescendingRanker,
    WorkloadAscendingRanker,
)
from synthorg.engine.assignment.scoring_based import ScoringBasedAssignmentStrategy
from synthorg.engine.routing.scorer import AgentTaskScorer
from synthorg.observability import get_logger
from synthorg.observability.events.task_assignment import (
    TASK_ASSIGNMENT_REGISTRY_BUILD,
)

if TYPE_CHECKING:
    from synthorg.communication.delegation.hierarchy import (
        HierarchyResolver,
    )

logger = get_logger(__name__)

_DEFAULT_SCORER = AgentTaskScorer()

# Single source of truth for the (name, ranker_factory) pairs that
# wire up every non-hierarchical scoring strategy. Both the static
# ``STRATEGY_MAP`` and ``build_strategy_map`` iterate this list so a
# new ranker only has to be added in one place. Hierarchical is
# special-cased because it needs the runtime ``HierarchyResolver``
# and a different pool filter.
_SCORING_STRATEGY_SPECS: tuple[tuple[str, Callable[[], CandidateRanker]], ...] = (
    (STRATEGY_NAME_ROLE_BASED, ScoreDescendingRanker),
    (STRATEGY_NAME_LOAD_BALANCED, WorkloadAscendingRanker),
    (STRATEGY_NAME_COST_OPTIMIZED, CostDescendingRanker),
    (STRATEGY_NAME_AUCTION, AuctionBidRanker),
)


def _build_scoring_strategies(
    *,
    scorer: AgentTaskScorer,
) -> dict[str, TaskAssignmentStrategy]:
    """Instantiate the non-hierarchical ScoringBased strategies for ``scorer``.

    Args:
        scorer: The ``AgentTaskScorer`` to inject into every strategy.

    Returns:
        A fresh dict mapping each non-hierarchical strategy name to a
        configured ``ScoringBasedAssignmentStrategy``.
    """
    return {
        name: ScoringBasedAssignmentStrategy(
            name=name,
            scorer=scorer,
            pool_filter=IdentityPoolFilter(),
            ranker=ranker_factory(),
        )
        for name, ranker_factory in _SCORING_STRATEGY_SPECS
    }


# Excludes the hierarchical composition -- it requires a
# HierarchyResolver at construction. Use
# build_strategy_map(hierarchy=...) to get a complete map
# that includes all strategies.
STRATEGY_MAP: MappingProxyType[str, TaskAssignmentStrategy] = MappingProxyType(
    {
        STRATEGY_NAME_MANUAL: ManualAssignmentStrategy(),
        **_build_scoring_strategies(scorer=_DEFAULT_SCORER),
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
        TASK_ASSIGNMENT_REGISTRY_BUILD,
        has_hierarchy=hierarchy is not None,
        custom_scorer=scorer is not None,
    )

    strategies: dict[str, TaskAssignmentStrategy] = {
        STRATEGY_NAME_MANUAL: ManualAssignmentStrategy(),
        **_build_scoring_strategies(scorer=effective_scorer),
    }

    if hierarchy is not None:
        strategies[STRATEGY_NAME_HIERARCHICAL] = ScoringBasedAssignmentStrategy(
            name=STRATEGY_NAME_HIERARCHICAL,
            scorer=effective_scorer,
            pool_filter=HierarchicalPoolFilter(hierarchy),
            ranker=ScoreDescendingRanker(),
        )

    return MappingProxyType(strategies)
