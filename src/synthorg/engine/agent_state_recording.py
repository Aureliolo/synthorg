# module-kind: code
"""Writes an agent's live runtime state as a run progresses.

``AgentRuntimeState`` is the designed answer to "what is this agent doing
right now": its own turn count, its own accumulated spend, when it started
and when it last did anything. It shipped with a model, a repository
protocol and a table in both backends, and nothing ever wrote it, so the
cockpit answered the live question from the flight-recorder frame store
instead. Frames are built from a finished run, so while a run was in flight
every live row read ``turn_count=0``, ``cost=0``, ``last_active=None``, and
neither the stuck nor the runaway marker could fire.

This module supplies the writers. One per turn (through the loop's own
progress hook, so the state is as current as the run is) and one at the end
of the dispatch, marking the agent idle. Both are best-effort: watching a
run must never be able to fail it.

The state is the live answer only. Once a run finishes there is a recorded
one, and the cockpit reads that instead: see
``engine/cockpit/service.py``, which prefers this row while the agent still
holds the task and the frames afterwards.
"""

from collections.abc import Callable

from synthorg.budget.currency import CurrencyCode
from synthorg.core.clock import Clock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.engine.agent_state import AgentRuntimeState, ExecutionStatus
from synthorg.engine.context import AgentContext
from synthorg.engine.loop_protocol import TurnObserver, TurnProgress
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.cockpit import (
    AGENT_RUNTIME_STATE_CLAIM_SKIPPED,
    AGENT_RUNTIME_STATE_IDLE_SKIPPED,
    AGENT_RUNTIME_STATE_WRITE_FAILED,
    TURN_OBSERVER_FAILED,
)
from synthorg.persistence.agent_state_protocol import AgentStateRepository

logger = get_logger(__name__)

#: Resolves the repository at call time, so a run started before persistence
#: connected still records state once it is.
AgentStateRepositoryProvider = Callable[[], AgentStateRepository | None]


async def _save(
    repository: AgentStateRepository,
    state: AgentRuntimeState,
) -> None:
    """Persist *state* for its own execution, logging rather than raising.

    The row is keyed by agent, but an agent can hold two dispatches at once,
    so the write is a compare-and-set on execution ownership rather than a
    plain upsert: it lands while the row is free or already this execution's,
    and is refused while a sibling holds it. A plain upsert made the row
    last-write-wins, so two overlapping runs alternated ownership every turn
    and the live view flipped between them; refusing instead means the first
    claim holds until it goes idle and releases the row, and the sibling reads
    from its recorded frames in the meantime, which is what the cockpit
    already does for an agent with no live row.

    Every caller is observing a run it must not disturb, so a storage fault
    here is reported and dropped.
    """
    execution_id = state.execution_id
    try:
        if execution_id is None:
            # Nothing to assert ownership with, so there is nothing to guard:
            # a state naming no execution cannot be stolen from a sibling
            # because it never claimed anything.
            await repository.save(state)
            return
        written = await repository.save_if_execution(
            state,
            expected_execution_id=execution_id,
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- best-effort side channel
        reraise_critical(exc)
        logger.warning(
            AGENT_RUNTIME_STATE_WRITE_FAILED,
            agent_id=state.agent_id,
            status=state.status.value,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    if not written:
        logger.debug(
            AGENT_RUNTIME_STATE_CLAIM_SKIPPED,
            agent_id=state.agent_id,
            execution_id=execution_id,
            status=state.status.value,
        )


async def mark_agent_running(
    *,
    repository_provider: AgentStateRepositoryProvider,
    context: AgentContext,
    currency: CurrencyCode,
    clock: Clock | None = None,
) -> None:
    """Record that a dispatch has started, before its first turn reports.

    The per-turn observer is not enough on its own. Both loops notify it
    only when a turn CONTINUES: a turn that returns a finished result
    returns it instead of reporting, so a run that completes in one turn
    never writes a row at all, and even a long run has no row until its
    first turn ends, which is one whole LLM call. Everything reading live
    state falls back to the recorded frames in that window, and for a run
    still in flight those read zero, which is the blind cockpit this
    module exists to fix.

    Args:
        repository_provider: Returns the current repository, or ``None``.
        context: The run's context at dispatch, carrying its identity,
            execution and start time.
        currency: The operator's active currency.
        clock: Time source for ``last_activity_at``.
    """
    repository = repository_provider()
    if repository is None:
        return
    await _save(
        repository,
        AgentRuntimeState.from_context(
            context,
            ExecutionStatus.EXECUTING,
            currency=currency,
            clock=clock,
        ),
    )


def make_runtime_state_observer(
    *,
    repository_provider: AgentStateRepositoryProvider,
    currency: CurrencyCode,
    clock: Clock | None = None,
) -> TurnObserver:
    """Build a turn observer that records the run's live state per turn.

    Args:
        repository_provider: Returns the current repository, or ``None``
            while persistence is unconnected.
        currency: The operator's active currency, which denominates the
            recorded balance.
        clock: Time source for ``last_activity_at``.

    Returns:
        A :data:`TurnObserver` that upserts the agent's live state.
    """

    async def _observe(progress: TurnProgress) -> None:
        repository = repository_provider()
        if repository is None:
            return
        await _save(
            repository,
            AgentRuntimeState.from_context(
                progress.context,
                ExecutionStatus.EXECUTING,
                currency=currency,
                clock=clock,
            ),
        )

    return _observe


def compose_turn_observers(
    *observers: TurnObserver | None,
) -> TurnObserver | None:
    """Fan one turn report out to every wired observer, in order.

    The loop reports progress once; how many things listen is the engine's
    business, so the composition lives here rather than in either loop.

    The listeners are independent, and composing them must not quietly make
    them a chain: they watch different things for different people (one
    streams to a connected operator, one keeps the live-activity row
    current), so a fault in either says nothing about the other. Run in
    sequence with no isolation, the first to raise would silently cost every
    later one its turn, and the loop's own wrapper would swallow it. Each is
    therefore guarded on its own. ``CancelledError`` is NOT caught: the run
    is being torn down, and every observer should stop.

    A lone observer is wrapped too. Handing it back unwrapped would be the
    cheaper composition of nothing, but the guard is not about the other
    observers: it is the promise that watching a run cannot fail it, and
    returning the bare callable breaks that promise in exactly the
    single-listener case that is the common one.

    Returns:
        A single observer calling each of *observers*, or ``None`` when none
        was supplied.
    """
    wired = tuple(observer for observer in observers if observer is not None)
    if not wired:
        return None

    async def _observe(progress: TurnProgress) -> None:
        for observer in wired:
            try:
                await observer(progress)
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                # lint-allow: swallow-ok -- watching a run must never fail it,
                # and one watcher's fault must not blind the others.
                reraise_critical(exc)
                logger.warning(
                    TURN_OBSERVER_FAILED,
                    turn_number=progress.turn_number,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )

    return _observe


async def mark_agent_idle(
    *,
    repository_provider: AgentStateRepositoryProvider,
    agent_id: str,
    execution_id: str,
    currency: CurrencyCode,
    clock: Clock | None = None,
) -> None:
    """Record that *agent_id* is no longer running *execution_id*.

    Called once the dispatch is over, including when it ended badly: a row
    left reading EXECUTING is what makes a finished agent look busy forever
    to ``get_active``, which is the query the live view is built on.

    The execution is named because the row is keyed by agent alone while an
    agent can hold more than one dispatch: the assignment cap is opt-in
    (``max_concurrent_tasks`` with workload data, both optional) and a wave
    dispatches its subtasks together, so a small roster can put two on one
    agent. Clearing unconditionally would then blank a SIBLING's live row the
    moment this one finished, and the read side treats an idle row as nothing
    running: the operator would see an actively working agent go idle, and
    its stuck and runaway detection would go blind, until its next turn wrote
    the row again. A whole turn is one LLM call. So the clear only lands when
    the row still belongs to the run doing the clearing, and that comparison
    is made by the write statement itself: reading the row first and saving
    after leaves the sibling a gap to claim the agent in, which is the very
    overwrite the check exists to prevent, just narrowed to a smaller window.

    Args:
        repository_provider: Returns the current repository, or ``None``.
        agent_id: The agent that has stopped.
        execution_id: The run that has stopped, checked against the stored
            row so a sibling's is left alone.
        currency: The operator's active currency, stored even at a zero
            balance so the row always carries an unambiguous unit.
        clock: Time source for ``last_activity_at``.
    """
    repository = repository_provider()
    if repository is None:
        return
    idle = AgentRuntimeState.idle(
        NotBlankStr(agent_id),
        currency=currency,
        clock=clock,
    )
    try:
        written = await repository.save_if_execution(
            idle, expected_execution_id=execution_id
        )
    except Exception as exc:  # noqa: BLE001 -- criticals re-raised
        # lint-allow: swallow-ok -- recording liveness is observation, and a
        # dispatch that finished must not be failed by the row describing it.
        reraise_critical(exc)
        logger.warning(
            AGENT_RUNTIME_STATE_WRITE_FAILED,
            agent_id=agent_id,
            operation="mark_idle",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return
    if not written:
        logger.debug(
            AGENT_RUNTIME_STATE_IDLE_SKIPPED,
            agent_id=agent_id,
            execution_id=execution_id,
        )


__all__ = [
    "AgentStateRepositoryProvider",
    "compose_turn_observers",
    "make_runtime_state_observer",
    "mark_agent_idle",
    "mark_agent_running",
]
