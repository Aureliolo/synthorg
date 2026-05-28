"""Pluggable flight-recorder sink and frame-building helpers.

A sink receives :class:`FlightRecorderFrame` records produced from an
agent run's :class:`ExecutionResult`. The default sink appends to the
persistence backend; the no-op sink discards frames. Recording is
best-effort: a failing sink logs and never propagates into the engine.
"""

from typing import TYPE_CHECKING, Final, Protocol, runtime_checkable

from pydantic import AwareDatetime

if TYPE_CHECKING:
    from collections.abc import Sequence

    from synthorg.providers.models import ChatMessage

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.enums import TaskStatus
from synthorg.engine.loop_protocol import (
    ExecutionResult,
    TerminationReason,
    TurnRecord,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.cockpit import (
    FLIGHT_RECORDER_FRAME_RECORDED,
    FLIGHT_RECORDER_RECORD_FAILED,
)
from synthorg.persistence.flight_recorder_protocol import (
    FlightRecorderFrame,
    FlightRecorderFrameRepository,
)
from synthorg.providers.enums import FinishReason, MessageRole

logger = get_logger(__name__)

#: Default cap on stored prompt/response summaries when no setting is
#: supplied at the call site (mirrors cockpit.flight_recorder_summary_max_chars).
DEFAULT_SUMMARY_MAX_CHARS: Final[int] = 2000

_TERMINATION_TO_STATUS: Final[dict[TerminationReason, TaskStatus]] = {
    TerminationReason.COMPLETED: TaskStatus.COMPLETED,
    TerminationReason.MAX_TURNS: TaskStatus.FAILED,
    TerminationReason.BUDGET_EXHAUSTED: TaskStatus.FAILED,
    TerminationReason.SHUTDOWN: TaskStatus.INTERRUPTED,
    TerminationReason.PARKED: TaskStatus.SUSPENDED,
    TerminationReason.STAGNATION: TaskStatus.FAILED,
    TerminationReason.ERROR: TaskStatus.FAILED,
}


@runtime_checkable
class FlightRecorderSink(Protocol):
    """Receives flight-recorder frames produced from an agent run."""

    async def record_frames(self, frames: tuple[FlightRecorderFrame, ...]) -> None:
        """Persist a run's frames. Best-effort; never raises into the engine."""
        ...


class PersistenceFlightRecorderSink:
    """Default sink: append frames to the persistence backend."""

    def __init__(self, repository: FlightRecorderFrameRepository) -> None:
        self._repository = repository

    async def record_frames(self, frames: tuple[FlightRecorderFrame, ...]) -> None:
        """Persist the run's frames as one batch; a failure logs, not raises.

        Recording runs after the agent loop has finished, so it is off
        the per-turn hot path; guarding here keeps a transient storage
        fault from turning a successful run into a failed one. The
        batch lands in a single transaction (``append_many``), so a
        partial finalise is not observable -- either every frame is
        persisted or none are.
        """
        if not frames:
            return
        try:
            await self._repository.append_many(frames)
        except Exception as exc:
            # ``reraise_critical`` ensures system errors escape so the
            # engine still gets the operator-fatal signal it expects
            # at the per-turn boundary. The recording path is best-
            # effort for storage faults only.
            reraise_critical(exc)
            logger.warning(
                FLIGHT_RECORDER_RECORD_FAILED,
                execution_id=frames[0].execution_id,
                turn_index=frames[0].turn_index,
                batch_size=len(frames),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return
        logger.debug(
            FLIGHT_RECORDER_FRAME_RECORDED,
            execution_id=frames[0].execution_id,
            count=len(frames),
        )


class NoOpFlightRecorderSink:
    """Backstop sink that discards frames (recording disabled)."""

    async def record_frames(self, frames: tuple[FlightRecorderFrame, ...]) -> None:
        """Discard the frames."""
        del frames


def build_flight_recorder_sink(
    repository: FlightRecorderFrameRepository | None,
    *,
    enabled: bool = True,
    strategy: str = "persistence",
) -> FlightRecorderSink:
    """Select the configured recorder sink.

    Returns:
        A :class:`NoOpFlightRecorderSink` when recording is disabled,
        the strategy is ``"noop"``, or no repository is available;
        otherwise the persistence-backed sink.
    """
    if not enabled or strategy == "noop" or repository is None:
        return NoOpFlightRecorderSink()
    return PersistenceFlightRecorderSink(repository)


def _truncate(text: str | None, max_chars: int) -> str | None:
    """Trim *text* to *max_chars*, returning ``None`` when empty.

    Returns:
        The first ``max_chars`` characters of ``text``, or ``None``
        when ``text`` is falsy.
    """
    if not text:
        return None
    return text[:max_chars]


def _classify_decision(turn: TurnRecord) -> str:
    """Classify a turn's outcome for the replay decision label.

    Returns:
        ``"tool_call"`` when the turn invoked tools; ``"completed"``
        for a plain STOP finish; otherwise the literal finish-reason
        value.
    """
    if turn.tool_calls_made:
        return "tool_call"
    if turn.finish_reason is FinishReason.STOP:
        return "completed"
    return turn.finish_reason.value


def build_frames(  # noqa: PLR0913 -- keyword-only frame builder, all required
    execution_result: ExecutionResult,
    *,
    execution_id: str,
    agent_id: str,
    task_id: str | None,
    summary_max_chars: int = DEFAULT_SUMMARY_MAX_CHARS,
    clock: Clock | None = None,
) -> tuple[FlightRecorderFrame, ...]:
    """Build one frame per turn from a finished run's execution result.

    Response content is taken from the assistant messages in the final
    conversation. Pairing is by ``turn.turn_number - 1`` (1-based turn
    index into the 0-based assistant-message list) so a *resumed* run --
    where ``execution_result.turns`` carries only the new turns while
    ``execution_result.context.conversation`` holds the full history --
    still correlates each turn with its actual assistant message instead
    of the first one in the full history. The terminal turn carries the
    run's outcome status; earlier turns are ``IN_PROGRESS``.

    Returns:
        Tuple of :class:`FlightRecorderFrame`, one per turn, with the
        run's terminal status stamped on the final frame and
        ``IN_PROGRESS`` on earlier frames.
    """
    timestamp = (clock or SystemClock()).now()
    assistant_messages = [
        msg
        for msg in execution_result.context.conversation
        if msg.role is MessageRole.ASSISTANT
    ]
    terminal_status = _TERMINATION_TO_STATUS.get(
        execution_result.termination_reason,
        TaskStatus.IN_PROGRESS,
    )
    last_index = len(execution_result.turns) - 1
    return tuple(
        _frame_for_turn(
            turn,
            execution_id=execution_id,
            agent_id=agent_id,
            task_id=task_id,
            response=_response_for_turn(turn, assistant_messages),
            status=terminal_status if index == last_index else TaskStatus.IN_PROGRESS,
            timestamp=timestamp,
            summary_max_chars=summary_max_chars,
        )
        for index, turn in enumerate(execution_result.turns)
    )


def _response_for_turn(
    turn: TurnRecord, assistant_messages: Sequence[ChatMessage]
) -> str | None:
    """Pick the assistant message at ``turn.turn_number - 1`` (or None).

    A resumed run's ``turn_number`` is 1-based against the full history,
    so subtracting one indexes into the assistant-message list correctly
    whether the run started fresh or resumed from a checkpoint. An
    out-of-range index (e.g. the conversation never recorded an
    assistant turn for that index) returns ``None`` rather than raising.

    Returns:
        The matching assistant message content; ``None`` when the
        turn index is outside the assistant-message range.
    """
    msg_index = turn.turn_number - 1
    if 0 <= msg_index < len(assistant_messages):
        return assistant_messages[msg_index].content
    return None


def _frame_for_turn(  # noqa: PLR0913 -- per-turn frame fields, all required
    turn: TurnRecord,
    *,
    execution_id: str,
    agent_id: str,
    task_id: str | None,
    response: str | None,
    status: TaskStatus,
    timestamp: AwareDatetime,
    summary_max_chars: int,
) -> FlightRecorderFrame:
    """Build one flight-recorder frame from a turn record.

    Returns:
        A :class:`FlightRecorderFrame` carrying the turn's summary,
        decision, tool calls, token / cost / status fields.
    """
    return FlightRecorderFrame(
        execution_id=execution_id,
        task_id=task_id,
        agent_id=agent_id,
        turn_index=turn.turn_number,
        timestamp=timestamp,
        response_summary=_truncate(response, summary_max_chars),
        decision=_classify_decision(turn),
        tool_calls=tuple(turn.tool_calls_made),
        input_tokens=turn.input_tokens,
        output_tokens=turn.output_tokens,
        cost=turn.cost,
        status=status,
    )
