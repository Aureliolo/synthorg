"""Pluggable flight-recorder sink and frame-building helpers.

A sink receives :class:`FlightRecorderFrame` records produced from an
agent run's :class:`ExecutionResult`. The default sink appends to the
persistence backend; the no-op sink discards frames. Recording is
best-effort: a failing sink logs and never propagates into the engine.
"""

from typing import Final, Protocol, runtime_checkable

from pydantic import AwareDatetime  # noqa: TC002 -- runtime annotation

from synthorg.core.clock import Clock, SystemClock
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
        """Append each frame; a failure on one frame is logged, not raised.

        Recording runs after the agent loop has finished, so it is off
        the per-turn hot path; guarding here keeps a transient storage
        fault from turning a successful run into a failed one.
        """
        recorded = 0
        for frame in frames:
            try:
                await self._repository.append(frame)
                recorded += 1
            except Exception as exc:
                logger.warning(
                    FLIGHT_RECORDER_RECORD_FAILED,
                    execution_id=frame.execution_id,
                    turn_index=frame.turn_index,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
        if recorded:
            logger.debug(
                FLIGHT_RECORDER_FRAME_RECORDED,
                execution_id=frames[0].execution_id,
                count=recorded,
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

    Returns a :class:`NoOpFlightRecorderSink` when recording is disabled,
    the strategy is ``"noop"``, or no repository is available; otherwise
    the persistence-backed sink.
    """
    if not enabled or strategy == "noop" or repository is None:
        return NoOpFlightRecorderSink()
    return PersistenceFlightRecorderSink(repository)


def _truncate(text: str | None, max_chars: int) -> str | None:
    """Trim *text* to *max_chars*, returning ``None`` when empty."""
    if not text:
        return None
    return text[:max_chars]


def _classify_decision(turn: TurnRecord) -> str:
    """Classify a turn's outcome for the replay decision label."""
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
    conversation, paired with turns in order. The terminal turn carries
    the run's outcome status; earlier turns are ``IN_PROGRESS``.
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
            response=(
                assistant_messages[index].content
                if index < len(assistant_messages)
                else None
            ),
            status=terminal_status if index == last_index else TaskStatus.IN_PROGRESS,
            timestamp=timestamp,
            summary_max_chars=summary_max_chars,
        )
        for index, turn in enumerate(execution_result.turns)
    )


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
    """Build one flight-recorder frame from a turn record."""
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
