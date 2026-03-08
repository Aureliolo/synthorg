"""Task assignment engine.

Assigns tasks to agents using pluggable strategies: manual
designation, role-based scoring, or load-balanced selection.
"""

from ai_company.engine.assignment.models import (
    AgentWorkload,
    AssignmentCandidate,
    AssignmentRequest,
    AssignmentResult,
)
from ai_company.engine.assignment.protocol import TaskAssignmentStrategy
from ai_company.engine.assignment.service import TaskAssignmentService
from ai_company.engine.assignment.strategies import (
    STRATEGY_MAP,
    STRATEGY_NAME_LOAD_BALANCED,
    STRATEGY_NAME_MANUAL,
    STRATEGY_NAME_ROLE_BASED,
    LoadBalancedAssignmentStrategy,
    ManualAssignmentStrategy,
    RoleBasedAssignmentStrategy,
)

__all__ = [
    "STRATEGY_MAP",
    "STRATEGY_NAME_LOAD_BALANCED",
    "STRATEGY_NAME_MANUAL",
    "STRATEGY_NAME_ROLE_BASED",
    "AgentWorkload",
    "AssignmentCandidate",
    "AssignmentRequest",
    "AssignmentResult",
    "LoadBalancedAssignmentStrategy",
    "ManualAssignmentStrategy",
    "RoleBasedAssignmentStrategy",
    "TaskAssignmentService",
    "TaskAssignmentStrategy",
]
