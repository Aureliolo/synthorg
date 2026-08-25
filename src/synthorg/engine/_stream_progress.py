# module-kind: code
"""Best-effort AG-UI progress projection for live task execution.

Publishes run-lifecycle and per-turn progress onto the shared
:class:`EventStreamHub`, keyed by ``session_id == task_id``, so the
dashboard's AG-UI SSE stream (``/events/stream``) can render live progress
for a running task instead of the operator staring at a silent queue
between the proposal and the completion review.

Every publish is best-effort: a missing hub or a publish failure is
swallowed (criticals re-raised) so progress projection can never disturb
the execution it is observing.
"""

import asyncio
from types import MappingProxyType

from synthorg.communication.event_stream.stream import EventStreamHub
from synthorg.communication.event_stream.types import AgUiEventType
from synthorg.core.critical_errors import reraise_critical
from synthorg.engine.loop_protocol import (
    TerminationReason,
    TurnObserver,
    TurnProgress,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.execution import EXECUTION_ENGINE_ERROR

logger = get_logger(__name__)

# Terminal reason -> AG-UI run event. Every reason that ENDS a run without a
# resume is mapped: a stuck/exhausted run (MAX_TURNS, BUDGET_EXHAUSTED,
# STAGNATION) projects RUN_ERROR because, like a hard error, it needs a human
# and the live panel must not hang on "Working". The reasons that end no run
# are enumerated in _SILENT_TERMINATION_REASONS below.
_TERMINAL_RUN_EVENT: MappingProxyType[TerminationReason, AgUiEventType] = (
    MappingProxyType(
        {
            TerminationReason.COMPLETED: AgUiEventType.RUN_FINISHED,
            TerminationReason.NO_OP: AgUiEventType.RUN_FINISHED,
            TerminationReason.ERROR: AgUiEventType.RUN_ERROR,
            TerminationReason.MAX_TURNS: AgUiEventType.RUN_ERROR,
            TerminationReason.BUDGET_EXHAUSTED: AgUiEventType.RUN_ERROR,
            TerminationReason.STAGNATION: AgUiEventType.RUN_ERROR,
        }
    )
)

# Reasons that intentionally project NO terminal event because the run is not
# over: it resumes (shutdown/park) or was intentionally halted (cancel).
_SILENT_TERMINATION_REASONS: frozenset[TerminationReason] = frozenset(
    {
        TerminationReason.SHUTDOWN,
        TerminationReason.PARKED,
        TerminationReason.CANCELLED,
    }
)

# Every TerminationReason must be a deliberate choice: mapped to a terminal
# event above, or listed as silent. A new reason added to the enum without
# either is caught here at import, not by a run hanging on "Working".
if set(_TERMINAL_RUN_EVENT) | _SILENT_TERMINATION_REASONS != set(TerminationReason):
    _unclassified = set(TerminationReason) - (
        set(_TERMINAL_RUN_EVENT) | _SILENT_TERMINATION_REASONS
    )
    msg = f"Unclassified TerminationReason(s) for progress projection: {_unclassified}"
    raise RuntimeError(msg)


async def _publish(
    hub: EventStreamHub,
    *,
    session_id: str,
    event_type: AgUiEventType,
    agent_id: str,
    payload: dict[str, object],
) -> None:
    """Publish one progress event to the hub, swallowing best-effort failures.

    Raises:
        CancelledError: Propagated so a client disconnect / shutdown halts
            the run rather than being masked as a projection failure.
    """
    try:
        await hub.publish_raw(
            session_id=session_id,
            event_type=event_type,
            agent_id=agent_id,
            payload=payload,
        )
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 -- progress projection is best-effort
        # lint-allow: swallow-ok -- best-effort side channel
        reraise_critical(exc)
        logger.warning(
            EXECUTION_ENGINE_ERROR,
            agent_id=agent_id,
            task_id=session_id,
            context="AG-UI progress projection failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )


async def publish_run_started(
    hub: EventStreamHub, *, task_id: str, agent_id: str
) -> None:
    """Project a ``RUN_STARTED`` event for a task run onto the hub."""
    await _publish(
        hub,
        session_id=task_id,
        event_type=AgUiEventType.RUN_STARTED,
        agent_id=agent_id,
        payload={"task_id": task_id},
    )


async def publish_run_terminated(
    hub: EventStreamHub,
    *,
    task_id: str,
    agent_id: str,
    reason: TerminationReason,
) -> None:
    """Project the terminal run event (finished/errored) for a task run.

    A termination reason that does not end the run (shutdown, parked,
    cancelled) is a no-op; every ending reason projects RUN_FINISHED or
    RUN_ERROR so the live panel never hangs on a silent stop.
    """
    event = _TERMINAL_RUN_EVENT.get(reason)
    if event is None:
        return
    await _publish(
        hub,
        session_id=task_id,
        event_type=event,
        agent_id=agent_id,
        payload={"task_id": task_id, "reason": reason.value},
    )


def make_turn_observer(
    hub: EventStreamHub, *, task_id: str, agent_id: str
) -> TurnObserver:
    """Build a per-run turn observer that projects step progress.

    The returned observer follows the :data:`TurnObserver` contract: the loop
    invokes it with each turn's requested tool names, and it projects a
    ``TOOL_CALL_START`` event carrying the index and labels so the operator
    sees the run making progress turn by turn.

    Returns:
        A :data:`TurnObserver` bound to this run's task id + agent.
    """

    async def _observe(progress: TurnProgress) -> None:
        await _publish(
            hub,
            session_id=task_id,
            event_type=AgUiEventType.TOOL_CALL_START,
            agent_id=agent_id,
            payload={
                "task_id": task_id,
                "turn": progress.turn_number,
                "tools": list(progress.tool_names),
            },
        )

    return _observe
