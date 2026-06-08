"""Dispatcher factory: maps ``CoordinationTopology`` to a dispatcher instance."""

from synthorg.core.clock import Clock
from synthorg.core.task_enums import CoordinationTopology
from synthorg.engine.coordination.centralized_dispatcher import CentralizedDispatcher
from synthorg.engine.coordination.context_dependent_dispatcher import (
    ContextDependentDispatcher,
)
from synthorg.engine.coordination.decentralized_dispatcher import (
    DecentralizedDispatcher,
)
from synthorg.engine.coordination.dispatcher_types import TopologyDispatcher
from synthorg.engine.coordination.sas_dispatcher import SasDispatcher
from synthorg.observability import get_logger
from synthorg.observability.events.coordination import (
    COORDINATION_PHASE_FAILED,
    COORDINATION_TOPOLOGY_RESOLVED,
)

logger = get_logger(__name__)


def select_dispatcher(
    topology: CoordinationTopology,
    *,
    clock: Clock | None = None,
) -> TopologyDispatcher:
    """Select the appropriate dispatcher for a topology.

    Args:
        topology: The resolved coordination topology.
        clock: Time source threaded into the dispatcher and the
            shared workspace/wave helpers so elapsed instrumentation
            uses the injected seam. Defaults to ``SystemClock``.

    Returns:
        A dispatcher instance for the topology.

    Raises:
        ValueError: If AUTO topology is passed (must be resolved first).
    """
    dispatcher: TopologyDispatcher
    match topology:
        case CoordinationTopology.SAS:
            dispatcher = SasDispatcher(clock=clock)
        case CoordinationTopology.CENTRALIZED:
            dispatcher = CentralizedDispatcher(clock=clock)
        case CoordinationTopology.DECENTRALIZED:
            dispatcher = DecentralizedDispatcher(clock=clock)
        case CoordinationTopology.CONTEXT_DEPENDENT:
            dispatcher = ContextDependentDispatcher(clock=clock)
        case _:
            msg = (
                f"Cannot dispatch topology {topology.value!r}: "
                "AUTO must be resolved before dispatch"
            )
            logger.warning(
                COORDINATION_PHASE_FAILED,
                phase="select_dispatcher",
                topology=topology.value,
                error=msg,
            )
            raise ValueError(msg)

    logger.debug(COORDINATION_TOPOLOGY_RESOLVED, topology=topology.value)
    return dispatcher
