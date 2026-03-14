"""Task assignment engine.

Assigns tasks to agents using pluggable strategies: manual
designation, role-based scoring, load-balanced selection,
cost-optimized selection, hierarchical delegation, or auction.
"""

from synthorg.engine.assignment.models import (
    AgentWorkload,
    AssignmentCandidate,
    AssignmentRequest,
    AssignmentResult,
)
from synthorg.engine.assignment.protocol import TaskAssignmentStrategy
from synthorg.engine.assignment.registry import (
    STRATEGY_MAP,
    build_strategy_map,
)
from synthorg.engine.assignment.service import TaskAssignmentService
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
    "AuctionAssignmentStrategy",
    "CostOptimizedAssignmentStrategy",
    "HierarchicalAssignmentStrategy",
    "LoadBalancedAssignmentStrategy",
    "ManualAssignmentStrategy",
    "RoleBasedAssignmentStrategy",
    "TaskAssignmentService",
    "TaskAssignmentStrategy",
    "build_strategy_map",
]
