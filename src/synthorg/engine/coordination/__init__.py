"""Multi-agent coordination engine.

Connects decomposition, routing, workspace isolation, and parallel
execution into an end-to-end pipeline orchestrated by topology-driven
dispatchers.
"""

from synthorg.engine.coordination.config import CoordinationConfig
from synthorg.engine.coordination.dispatchers import (
    CentralizedDispatcher,
    ContextDependentDispatcher,
    DecentralizedDispatcher,
    DispatchResult,
    SasDispatcher,
    TopologyDispatcher,
    select_dispatcher,
)
from synthorg.engine.coordination.factory import build_coordinator
from synthorg.engine.coordination.group_builder import build_execution_waves
from synthorg.engine.coordination.models import (
    CoordinationContext,
    CoordinationPhaseResult,
    CoordinationResult,
    CoordinationWave,
)
from synthorg.engine.coordination.section_config import CoordinationSectionConfig
from synthorg.engine.coordination.service import MultiAgentCoordinator

__all__ = [
    "CentralizedDispatcher",
    "ContextDependentDispatcher",
    "CoordinationConfig",
    "CoordinationContext",
    "CoordinationPhaseResult",
    "CoordinationResult",
    "CoordinationSectionConfig",
    "CoordinationWave",
    "DecentralizedDispatcher",
    "DispatchResult",
    "MultiAgentCoordinator",
    "SasDispatcher",
    "TopologyDispatcher",
    "build_coordinator",
    "build_execution_waves",
    "select_dispatcher",
]
