"""Dispatcher factory: maps ``CoordinationTopology`` to a dispatcher instance."""

from synthorg.core.clock import Clock
from synthorg.core.task_enums import CoordinationTopology
from synthorg.engine.coordination.assignment_writer import AssignmentWriter
from synthorg.engine.coordination.context_dependent_dispatcher import (
    ContextDependentDispatcher,
)
from synthorg.engine.coordination.dispatcher_types import TopologyDispatcher
from synthorg.engine.coordination.sas_dispatcher import SasDispatcher
from synthorg.engine.coordination.wave_dispatcher import WaveDispatcher
from synthorg.engine.middleware.orchestrator_strategy import OrchestratorStrategy
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
    orchestrator_strategy: OrchestratorStrategy | None = None,
    assignment_writer: AssignmentWriter | None = None,
) -> TopologyDispatcher:
    """Select the appropriate dispatcher for a topology.

    Args:
        topology: The resolved coordination topology.
        clock: Time source threaded into the dispatcher and the
            shared workspace/wave helpers so elapsed instrumentation
            uses the injected seam. Defaults to ``SystemClock``.
        orchestrator_strategy: Subtask-selection strategy injected into
            the centralized ``WaveDispatcher``. ``None`` keeps the
            original dispatch order (the ``naive`` default behaviour).
        assignment_writer: Persists each wave's assignments through the
            central engine before that wave runs. Every topology gets
            one: dispatching on a status the engine has not applied is
            the same defect whichever dispatcher does it.

    Returns:
        A dispatcher instance for the topology.

    Raises:
        ValueError: If AUTO topology is passed (must be resolved first).
    """
    dispatcher: TopologyDispatcher
    match topology:
        case CoordinationTopology.SAS:
            dispatcher = SasDispatcher(
                clock=clock,
                assignment_writer=assignment_writer,
            )
        case CoordinationTopology.CENTRALIZED:
            dispatcher = WaveDispatcher(
                clock=clock,
                isolation_required=False,
                topology_label="centralized",
                orchestrator_strategy=orchestrator_strategy,
                assignment_writer=assignment_writer,
            )
        case CoordinationTopology.DECENTRALIZED:
            dispatcher = WaveDispatcher(
                clock=clock,
                isolation_required=True,
                topology_label="decentralized",
                assignment_writer=assignment_writer,
            )
        case CoordinationTopology.CONTEXT_DEPENDENT:
            dispatcher = ContextDependentDispatcher(
                clock=clock,
                assignment_writer=assignment_writer,
            )
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
