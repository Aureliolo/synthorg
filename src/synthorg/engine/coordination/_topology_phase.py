# module-kind: code
"""Topology resolution as a recorded coordination phase.

Resolution itself is one call. What surrounds it is not: the provider may
read runtime settings or raise, and mixed-topology routing raises a
``CoordinationPhaseError`` carrying no phase marker of its own, so both have
to become a failed phase entry before the pipeline re-raises. Without that
entry the caller's ``partial_phases`` list skips the step entirely and an
operator reading it sees routing followed by dispatch, with no sign of where
the run actually died.
"""

from collections.abc import Callable
from typing import Final

from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task_enums import CoordinationTopology
from synthorg.engine.coordination.models import CoordinationPhaseResult
from synthorg.engine.errors import CoordinationPhaseError
from synthorg.engine.routing.models import RoutingResult
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.coordination import COORDINATION_PHASE_FAILED

logger = get_logger(__name__)

_PHASE: Final[str] = "resolve_topology"


def resolve_topology_phase(
    resolve: Callable[[RoutingResult], CoordinationTopology],
    routing_result: RoutingResult,
    phases: list[CoordinationPhaseResult],
    *,
    clock: Clock,
) -> CoordinationTopology:
    """Resolve the topology, recording the step whichever way it goes.

    Args:
        resolve: The coordinator's own resolution, called with the routing.
        routing_result: What dispatch will be built from.
        phases: The running phase list, appended to on failure.
        clock: Time seam supplying the duration.

    Returns:
        The resolved topology.

    Raises:
        CoordinationPhaseError: Whatever resolution refused, restated with
            the phase list as it stands, so the caller sees which phases
            completed before the failure.
    """
    start = clock.monotonic()
    try:
        return resolve(routing_result)
    except CoordinationPhaseError as phase_exc:
        # Logged here even though the coordinator logs its own mixed-topology
        # refusal: a provider-originated failure raising the same type would
        # otherwise reach the caller with no entry at all, and one line per
        # failure path is the contract.
        _record(
            phase_exc,
            start,
            phases,
            clock=clock,
            empty_routing_decisions=not routing_result.decisions,
        )
        raise CoordinationPhaseError(
            str(phase_exc),
            phase=_PHASE,
            partial_phases=tuple(phases),
        ) from phase_exc
    except Exception as exc:
        reraise_critical(exc)
        _record(exc, start, phases, clock=clock, empty_routing_decisions=None)
        msg = f"Topology resolution failed: {safe_error_description(exc)}"
        raise CoordinationPhaseError(
            msg,
            phase=_PHASE,
            partial_phases=tuple(phases),
        ) from exc


def _record(
    exc: Exception,
    start: float,
    phases: list[CoordinationPhaseResult],
    *,
    clock: Clock,
    empty_routing_decisions: bool | None,
) -> None:
    """Log the refusal and append its failed phase entry.

    Args:
        exc: What refused.
        start: The monotonic reading resolution began at.
        phases: The running phase list, appended to.
        clock: Time seam supplying the end reading.
        empty_routing_decisions: Whether routing produced no decisions,
            when that is the thing which explains the refusal; ``None``
            leaves it off the line entirely.
    """
    description = safe_error_description(exc)
    routing_shape: dict[str, object] = (
        {}
        if empty_routing_decisions is None
        else {"empty_routing_decisions": empty_routing_decisions}
    )
    logger.warning(
        COORDINATION_PHASE_FAILED,
        phase=_PHASE,
        error_type=type(exc).__name__,
        error=description,
        **routing_shape,
    )
    phases.append(
        CoordinationPhaseResult(
            phase=_PHASE,
            success=False,
            duration_seconds=clock.monotonic() - start,
            error=description,
        )
    )


__all__ = ["resolve_topology_phase"]
