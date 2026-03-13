"""Multi-agent coordination engine.

Connects decomposition, routing, workspace isolation, and parallel
execution into an end-to-end pipeline orchestrated by topology-driven
dispatchers.
"""

from ai_company.engine.coordination.config import CoordinationConfig
from ai_company.engine.coordination.dispatchers import (
    CentralizedDispatcher,
    ContextDependentDispatcher,
    DecentralizedDispatcher,
    DispatchResult,
    SasDispatcher,
    TopologyDispatcher,
    select_dispatcher,
)
from ai_company.engine.coordination.group_builder import build_execution_waves
from ai_company.engine.coordination.models import (
    CoordinationContext,
    CoordinationPhaseResult,
    CoordinationResult,
    CoordinationWave,
)
from ai_company.engine.coordination.service import MultiAgentCoordinator

__all__ = [
    "CentralizedDispatcher",
    "ContextDependentDispatcher",
    "CoordinationConfig",
    "CoordinationContext",
    "CoordinationPhaseResult",
    "CoordinationResult",
    "CoordinationWave",
    "DecentralizedDispatcher",
    "DispatchResult",
    "MultiAgentCoordinator",
    "SasDispatcher",
    "TopologyDispatcher",
    "build_execution_waves",
    "select_dispatcher",
]
