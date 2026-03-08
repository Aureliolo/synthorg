"""Topology selection for decomposed tasks.

Implements DESIGN_SPEC Section 6.9 auto-selection heuristics
for coordination topologies.
"""

from typing import TYPE_CHECKING

from ai_company.core.enums import CoordinationTopology, TaskStructure
from ai_company.engine.routing.models import AutoTopologyConfig
from ai_company.observability import get_logger
from ai_company.observability.events.task_routing import (
    TASK_ROUTING_TOPOLOGY_AUTO_RESOLVED,
    TASK_ROUTING_TOPOLOGY_SELECTED,
)

if TYPE_CHECKING:
    from ai_company.core.task import Task
    from ai_company.engine.decomposition.models import DecompositionPlan

logger = get_logger(__name__)


class TopologySelector:
    """Selects coordination topology for decomposed tasks.

    Uses explicit overrides when set, otherwise applies heuristic
    rules based on task structure and tool count.
    """

    __slots__ = ("_config",)

    def __init__(self, config: AutoTopologyConfig | None = None) -> None:
        self._config = config or AutoTopologyConfig()

    @property
    def config(self) -> AutoTopologyConfig:
        """Current topology configuration."""
        return self._config

    def select(
        self,
        task: Task,
        plan: DecompositionPlan,
    ) -> CoordinationTopology:
        """Select the coordination topology for a decomposed task.

        Args:
            task: The parent task.
            plan: The decomposition plan.

        Returns:
            The selected coordination topology.
        """
        # Explicit override takes precedence
        if task.coordination_topology != CoordinationTopology.AUTO:
            logger.debug(
                TASK_ROUTING_TOPOLOGY_SELECTED,
                task_id=task.id,
                topology=task.coordination_topology.value,
                source="explicit",
            )
            return task.coordination_topology

        # Auto-select based on structure
        structure = plan.task_structure
        tool_count = len(task.artifacts_expected)

        if structure == TaskStructure.SEQUENTIAL:
            topology = self._config.sequential_override
        elif structure == TaskStructure.PARALLEL:
            if tool_count > self._config.parallel_tool_threshold:
                topology = CoordinationTopology.DECENTRALIZED
            else:
                topology = self._config.parallel_default
        else:
            # MIXED or unknown
            topology = self._config.mixed_default

        logger.debug(
            TASK_ROUTING_TOPOLOGY_AUTO_RESOLVED,
            task_id=task.id,
            topology=topology.value,
            structure=structure.value,
            tool_count=tool_count,
        )

        return topology
