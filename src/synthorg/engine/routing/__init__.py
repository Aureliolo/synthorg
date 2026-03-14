"""Task routing engine.

Routes decomposed subtasks to appropriate agents based on skill
matching, role alignment, and topology selection.
"""

from synthorg.engine.routing.models import (
    AutoTopologyConfig,
    RoutingCandidate,
    RoutingDecision,
    RoutingResult,
)
from synthorg.engine.routing.scorer import AgentTaskScorer
from synthorg.engine.routing.service import TaskRoutingService
from synthorg.engine.routing.topology_selector import TopologySelector

__all__ = [
    "AgentTaskScorer",
    "AutoTopologyConfig",
    "RoutingCandidate",
    "RoutingDecision",
    "RoutingResult",
    "TaskRoutingService",
    "TopologySelector",
]
