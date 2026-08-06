"""Topology selection for decomposed tasks.

Implements the Engine design page auto-selection heuristics
for coordination topologies.
"""

from synthorg.core.task import Task
from synthorg.core.task_enums import CoordinationTopology, TaskStructure
from synthorg.engine.decomposition.models import DecompositionPlan
from synthorg.engine.errors import DecompositionError
from synthorg.engine.routing.models import AutoTopologyConfig
from synthorg.observability import get_logger
from synthorg.observability.events.task_routing import (
    TASK_ROUTING_TOPOLOGY_AUTO_RESOLVED,
    TASK_ROUTING_TOPOLOGY_SELECTED,
)

logger = get_logger(__name__)


class TopologySelector:
    """Selects coordination topology for decomposed tasks.

    Uses explicit overrides when set, otherwise applies heuristic
    rules based on task structure and artifact count.
    Implements the auto-selection heuristics from the Engine design
    page.
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

        Raises:
            DecompositionError: If the plan's structure was never resolved,
                which means it did not come through ``DecompositionService``.
                Picking a topology from a structure nobody chose would route
                the work on a guess the operator never sees.
        """
        # ``DecompositionService`` resolves an undeclared structure before the
        # plan leaves it, so AUTO here means the plan never went through it.
        # The sibling check in ``plan_mapping.plan_from_decomposition`` refuses
        # the same state, and routing on a guessed structure is the same defect
        # one layer over. Checked ahead of the explicit override because the
        # override decides which topology to route to, not whether the plan
        # reaching this point is one the system is willing to route at all.
        structure = plan.task_structure
        if structure is TaskStructure.AUTO:
            msg = "Topology selection reached an unresolved task_structure"
            logger.warning(
                TASK_ROUTING_TOPOLOGY_AUTO_RESOLVED,
                task_id=task.id,
                structure=structure.value,
                error=msg,
            )
            raise DecompositionError(msg)

        if task.coordination_topology != CoordinationTopology.AUTO:
            logger.debug(
                TASK_ROUTING_TOPOLOGY_SELECTED,
                task_id=task.id,
                topology=task.coordination_topology.value,
                source="explicit",
            )
            return task.coordination_topology

        artifact_count = len(task.artifacts_expected)

        if structure is TaskStructure.PARALLEL:
            if artifact_count > self._config.parallel_artifact_threshold:
                topology = CoordinationTopology.DECENTRALIZED
            else:
                topology = self._config.parallel_default
        elif structure is TaskStructure.MIXED:
            topology = self._config.mixed_default
        else:
            topology = self._config.sequential_override

        logger.debug(
            TASK_ROUTING_TOPOLOGY_AUTO_RESOLVED,
            task_id=task.id,
            topology=topology.value,
            structure=structure.value,
            artifact_count=artifact_count,
        )

        return topology
