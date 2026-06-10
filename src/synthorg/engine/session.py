"""Stateless session replay from observability event log.

Reconstructs an ``AgentContext`` from the structured event stream
recorded during a previous execution.  This is a lighter-weight
alternative to full checkpoint/resume: read-only reconstruction
that enables brain-failure recovery without persistence dependencies.

Terminology follows the managed-agents engineering pattern:

- **Brain**: inference loop (``agent_engine.py``, ``AgentContext``, loop protocol)
- **Hands**: tool execution (``ToolInvoker``, ``tools/sandbox/``, credential proxy)
- **Session**: durable event history (``observability/events/``, replay)
"""

import copy
from typing import Protocol, Self, cast, runtime_checkable

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    model_validator,
)

from synthorg.core.agent import AgentIdentity
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task import Task
from synthorg.core.task_enums import TaskStatus
from synthorg.core.types import NotBlankStr
from synthorg.engine.context import DEFAULT_MAX_TURNS, AgentContext
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.execution import (
    EXECUTION_CONTEXT_CREATED,
    EXECUTION_CONTEXT_TURN,
    EXECUTION_ENGINE_START,
    EXECUTION_TASK_TRANSITION,
)
from synthorg.observability.events.session import (
    SESSION_REPLAY_COMPLETE,
    SESSION_REPLAY_ERROR,
    SESSION_REPLAY_NO_EVENTS,
    SESSION_REPLAY_PARTIAL,
    SESSION_REPLAY_START,
    SESSION_TASK_STATUS_CHANGED,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, TokenUsage, add_token_usage

logger = get_logger(__name__)

_COMPLETENESS_THRESHOLD: float = 0.85
"""Replay completeness at or above which the replay is considered full."""


# ── Models ────────────────────────────────────────────────────────


class SessionEvent(BaseModel):
    """A single event from the observability event log.

    Attributes:
        event_name: Dotted event constant (e.g. ``"execution.context.turn"``).
        timestamp: When the event was recorded.
        execution_id: Execution run this event belongs to.
        data: Structured event payload (deep-copied at construction).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    event_name: NotBlankStr = Field(description="Dotted event constant")
    timestamp: AwareDatetime = Field(description="Event timestamp")
    execution_id: NotBlankStr = Field(
        description="Execution run this event belongs to",
    )
    data: dict[str, object] = Field(
        default_factory=dict,
        description="Structured event payload",
    )

    @model_validator(mode="after")
    def _deepcopy_data(self) -> Self:
        """Defensive copy so callers cannot mutate the frozen model.

        Returns:
            ``self`` with ``data`` replaced by a deepcopy.
        """
        object.__setattr__(self, "data", copy.deepcopy(self.data))
        return self


class ReplayResult(BaseModel):
    """Result of a session replay attempt.

    Attributes:
        context: Reconstructed agent context (may be partial).
        replay_completeness: Fraction of expected state recovered
            (0.0 = nothing, 1.0 = everything).
        events_processed: Number of events consumed during replay.
        events_total: Total events found for this execution.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    context: AgentContext = Field(
        description="Reconstructed agent context",
    )
    replay_completeness: float = Field(
        ge=0.0,
        le=1.0,
        description="Fraction of expected state recovered",
    )
    events_processed: int = Field(
        ge=0,
        description="Events consumed during replay",
    )
    events_total: int = Field(
        ge=0,
        description="Total events found for this execution",
    )

    @model_validator(mode="after")
    def _validate_processed_le_total(self) -> Self:
        """Ensure events_processed does not exceed events_total.

        Returns:
            The unmodified ``self``.

        Raises:
            ValueError: If ``events_processed > events_total``.
        """
        if self.events_processed > self.events_total:
            msg = (
                f"events_processed ({self.events_processed}) "
                f"cannot exceed events_total ({self.events_total})"
            )
            raise ValueError(msg)
        return self


# ── Protocol ──────────────────────────────────────────────────────


@runtime_checkable
class EventReader(Protocol):
    """Read observability events by execution ID.

    Concrete implementations may read from structured log files,
    OTLP backends, or the Postgres ``observability_events`` table.
    """

    async def read_events(
        self,
        execution_id: str,
    ) -> tuple[SessionEvent, ...]:
        """Return events for the given execution, ordered by timestamp."""
        ...


# ── Session replay ────────────────────────────────────────────────


class Session:
    """Stateless session replay from the observability event log.

    Provides ``replay()`` to reconstruct an ``AgentContext`` from
    the event stream of a previous (possibly crashed) execution.
    """

    @staticmethod
    async def replay(
        *,
        execution_id: str,
        event_reader: EventReader,
        identity: AgentIdentity,
        task: Task | None = None,
        max_turns: int = DEFAULT_MAX_TURNS,
    ) -> ReplayResult:
        """Reconstruct an ``AgentContext`` from the event log.

        Best-effort: if the event stream is incomplete, returns a
        partial context with ``replay_completeness < 1.0``.

        Args:
            execution_id: The execution to replay.
            event_reader: Source of observability events.
            identity: Agent identity for the reconstructed context.
            task: Optional task to bind to the context.
            max_turns: Maximum turns for the reconstructed context.

        Returns:
            ``ReplayResult`` with the reconstructed context and
            completeness score.
        """
        logger.info(
            SESSION_REPLAY_START,
            execution_id=execution_id,
            agent_id=str(identity.id),
        )

        try:
            events = await event_reader.read_events(execution_id)
        except Exception as exc:
            logger.warning(
                SESSION_REPLAY_ERROR,
                execution_id=execution_id,
                reason="failed to read events from event_reader",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise

        # Filter out events that don't belong to this execution
        # (defense-in-depth against buggy EventReader implementations).
        valid_events = tuple(e for e in events if e.execution_id == execution_id)
        if len(valid_events) < len(events):
            logger.warning(
                SESSION_REPLAY_ERROR,
                execution_id=execution_id,
                reason="event_reader returned events for other executions",
                expected=len(events),
                kept=len(valid_events),
            )

        if not valid_events:
            logger.info(
                SESSION_REPLAY_NO_EVENTS,
                execution_id=execution_id,
            )
            ctx = AgentContext.from_identity(
                identity,
                task=task,
                max_turns=max_turns,
            )
            ctx = ctx.model_copy(update={"execution_id": execution_id})
            return ReplayResult(
                context=ctx,
                replay_completeness=0.0,
                events_processed=0,
                events_total=0,
            )

        sorted_events = sorted(valid_events, key=lambda e: e.timestamp)
        return _replay_from_events(
            sorted_events=sorted_events,
            identity=identity,
            task=task,
            max_turns=max_turns,
            execution_id=execution_id,
        )


# ── Internal replay helpers ───────────────────────────────────────


def _apply_turn_event(
    ctx: AgentContext,
    event: SessionEvent,
) -> tuple[AgentContext, int, float]:
    """Apply a single turn event to the context.

    Returns:
        Tuple of (updated context, turn number, cost).

    Raises:
        KeyError: If ``turn`` key is missing.
        ValueError: If ``turn`` < 1 or non-numeric.
    """
    turn = event.data.get("turn")
    if turn is None:
        msg = "Missing 'turn' in EXECUTION_CONTEXT_TURN event"
        raise KeyError(msg)
    turn = int(cast("int | str", turn))
    if turn < 1:
        msg = f"Turn number must be >= 1, got {turn}"
        raise ValueError(msg)
    cost = float(cast("float | str", event.data.get("cost", 0.0)))

    usage = TokenUsage(input_tokens=0, output_tokens=0, cost=cost)
    replay_msg = ChatMessage(
        role=MessageRole.ASSISTANT,
        content=f"[replayed turn {turn}]",
    )
    updates: dict[str, object] = {
        "turn_count": ctx.turn_count + 1,
        "conversation": (*ctx.conversation, replay_msg),
        "accumulated_cost": add_token_usage(ctx.accumulated_cost, usage),
    }
    if ctx.task_execution is not None:
        updates["task_execution"] = ctx.task_execution.with_cost(usage)
    return ctx.model_copy(update=updates), turn, cost


def _apply_transition_event(
    ctx: AgentContext,
    event: SessionEvent,
) -> AgentContext:
    """Apply a task-transition event to the context.

    Returns:
        A copy of ``ctx`` with the task status replaced when the event
        carries a ``target_status``; the original ``ctx`` when no
        transition applies.
    """
    target = event.data.get("target_status")
    if target is not None and ctx.task_execution is not None:
        from_status = ctx.task_execution.status
        new_status = TaskStatus(str(target))
        task_id = ctx.task_execution.task.id
        ctx = ctx.model_copy(
            update={
                "task_execution": ctx.task_execution.model_copy(
                    update={"status": new_status},
                ),
            },
        )
        logger.info(
            SESSION_TASK_STATUS_CHANGED,
            task_id=task_id,
            from_status=from_status.value,
            to_status=new_status.value,
        )
    return ctx


# ── Internal replay logic ─────────────────────────────────────────


def _replay_from_events(
    *,
    sorted_events: list[SessionEvent],
    identity: AgentIdentity,
    task: Task | None,
    max_turns: int,
    execution_id: str,
) -> ReplayResult:
    """Walk sorted events and reconstruct AgentContext.

    Returns:
        A :class:`ReplayResult` carrying the reconstructed context,
        the completeness fraction, and the events-processed /
        events-total counts.
    """
    ctx = AgentContext.from_identity(
        identity,
        task=task,
        max_turns=max_turns,
    )
    # Preserve original execution lineage and seed started_at from
    # the earliest event timestamp.
    ctx = ctx.model_copy(
        update={
            "execution_id": execution_id,
            "started_at": sorted_events[0].timestamp,
        },
    )

    # Tracking for completeness scoring.
    found_engine_start = False
    found_context_created = False
    seen_turns: set[int] = set()
    turn_numbers: list[int] = []
    total_cost = 0.0
    found_transition = False
    processed = 0

    for event in sorted_events:
        try:
            processed += 1
            name = event.event_name

            if name == EXECUTION_ENGINE_START:
                found_engine_start = True

            elif name == EXECUTION_CONTEXT_CREATED:
                found_context_created = True

            elif name == EXECUTION_CONTEXT_TURN:
                # Parse turn number before applying to skip duplicates.
                raw_turn = event.data.get("turn")
                if (
                    raw_turn is not None
                    and int(cast("int | str", raw_turn)) in seen_turns
                ):
                    continue
                ctx, turn, cost = _apply_turn_event(ctx, event)
                seen_turns.add(turn)
                turn_numbers.append(turn)
                total_cost += cost

            elif name == EXECUTION_TASK_TRANSITION:
                found_transition = True
                ctx = _apply_transition_event(ctx, event)

        except (ValueError, TypeError, KeyError) as exc:
            logger.warning(
                SESSION_REPLAY_ERROR,
                execution_id=execution_id,
                event_name=event.event_name,
                reason="malformed event data",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                SESSION_REPLAY_ERROR,
                execution_id=execution_id,
                event_name=event.event_name,
                reason="unexpected error processing event",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    completeness = _compute_completeness(
        found_engine_start=found_engine_start,
        found_context_created=found_context_created,
        turn_numbers=turn_numbers,
        total_cost=total_cost,
        found_transition=found_transition,
    )

    event_name = (
        SESSION_REPLAY_COMPLETE
        if completeness >= _COMPLETENESS_THRESHOLD
        else SESSION_REPLAY_PARTIAL
    )
    logger.info(
        event_name,
        execution_id=execution_id,
        replay_completeness=completeness,
        turns_replayed=len(turn_numbers),
        events_processed=processed,
    )

    return ReplayResult(
        context=ctx,
        replay_completeness=completeness,
        events_processed=processed,
        events_total=len(sorted_events),
    )


def _compute_completeness(
    *,
    found_engine_start: bool,
    found_context_created: bool,
    turn_numbers: list[int],
    total_cost: float,
    found_transition: bool,
) -> float:
    """Compute replay completeness as a weighted additive score.

    Each condition contributes independently (capped at 1.0):

        Engine start event:          +0.15
        Context created event:       +0.10
        At least one turn event:     +0.20
        Contiguous turn sequence:    +0.25 (bonus on top of turn)
        Cost data in turn events:    +0.15
        Task transition events:      +0.15

    Returns:
        The clamped completeness score in ``[0.0, 1.0]``.
    """
    score = 0.0

    if found_engine_start:
        score += 0.15
    if found_context_created:
        score += 0.10
    if turn_numbers:
        score += 0.20
        # Deduplicate before contiguity check so duplicate turn
        # events (e.g. retransmitted events) don't penalize the score.
        unique_turns = sorted(set(turn_numbers))
        expected = list(range(1, len(unique_turns) + 1))
        if unique_turns == expected:
            score += 0.25
    if total_cost > 0.0:
        score += 0.15
    if found_transition:
        score += 0.15

    return min(score, 1.0)
