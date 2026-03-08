"""Task routing engine.

Routes decomposed subtasks to appropriate agents based on skill
matching, role alignment, and topology selection.
"""

from ai_company.engine.routing.models import (
    AutoTopologyConfig,
    RoutingCandidate,
    RoutingDecision,
    RoutingResult,
)
from ai_company.engine.routing.scorer import AgentTaskScorer
from ai_company.engine.routing.service import TaskRoutingService
from ai_company.engine.routing.topology_selector import TopologySelector

__all__ = [
    "AgentTaskScorer",
    "AutoTopologyConfig",
    "RoutingCandidate",
    "RoutingDecision",
    "RoutingResult",
    "TaskRoutingService",
    "TopologySelector",
]
