"""Task assignment engine.

Assigns tasks to agents using pluggable strategies: manual
designation, role-based scoring, load-balanced selection,
cost-optimized selection, hierarchical delegation, or auction.
"""

from synthorg.engine.assignment._shared import (
    STRATEGY_NAME_AUCTION,
    STRATEGY_NAME_COST_OPTIMIZED,
    STRATEGY_NAME_HIERARCHICAL,
    STRATEGY_NAME_LOAD_BALANCED,
    STRATEGY_NAME_MANUAL,
    STRATEGY_NAME_ROLE_BASED,
)
from synthorg.engine.assignment.manual import ManualAssignmentStrategy
from synthorg.engine.assignment.models import (
    AgentWorkload,
    AssignmentCandidate,
    AssignmentRequest,
    AssignmentResult,
)
from synthorg.engine.assignment.pool_filter_protocol import (
    CandidatePoolFilter,
    PoolFilterResult,
)
from synthorg.engine.assignment.pool_filters import (
    HierarchicalPoolFilter,
    IdentityPoolFilter,
)
from synthorg.engine.assignment.protocol import TaskAssignmentStrategy
from synthorg.engine.assignment.ranker_protocol import (
    CandidateRanker,
    RankingResult,
)
from synthorg.engine.assignment.rankers import (
    AuctionBidRanker,
    CostDescendingRanker,
    ScoreDescendingRanker,
    WorkloadAscendingRanker,
)
from synthorg.engine.assignment.registry import (
    STRATEGY_MAP,
    build_strategy_map,
)
from synthorg.engine.assignment.scoring_based import ScoringBasedAssignmentStrategy
from synthorg.engine.assignment.service import TaskAssignmentService

__all__ = [
    "STRATEGY_MAP",
    "STRATEGY_NAME_AUCTION",
    "STRATEGY_NAME_COST_OPTIMIZED",
    "STRATEGY_NAME_HIERARCHICAL",
    "STRATEGY_NAME_LOAD_BALANCED",
    "STRATEGY_NAME_MANUAL",
    "STRATEGY_NAME_ROLE_BASED",
    "AgentWorkload",
    "AssignmentCandidate",
    "AssignmentRequest",
    "AssignmentResult",
    "AuctionBidRanker",
    "CandidatePoolFilter",
    "CandidateRanker",
    "CostDescendingRanker",
    "HierarchicalPoolFilter",
    "IdentityPoolFilter",
    "ManualAssignmentStrategy",
    "PoolFilterResult",
    "RankingResult",
    "ScoreDescendingRanker",
    "ScoringBasedAssignmentStrategy",
    "TaskAssignmentService",
    "TaskAssignmentStrategy",
    "WorkloadAscendingRanker",
    "build_strategy_map",
]
