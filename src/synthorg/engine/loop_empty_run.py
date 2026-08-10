"""One corrective turn for a work run that has delivered nothing yet.

A live run died here: an agent answered a five-item build brief in prose on
turn 1 of 20, the zero-artifact guard failed the task, and nineteen turns went
unused. The guard is right that a silent no-op is a failure; it was firing
before the agent had been told what it missed.
"""

from synthorg.engine.context import AgentContext
from synthorg.engine.prompt_safety import TAG_TASK_DATA, wrap_untrusted
from synthorg.engine.resume_scope import is_resumed_run
from synthorg.execution.turn import TurnRecord
from synthorg.observability import get_logger
from synthorg.observability.events.execution import (
    EXECUTION_LOOP_EMPTY_RUN_NUDGED,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage

logger = get_logger(__name__)


def _nudge_message(
    ctx: AgentContext,
    turns: list[TurnRecord],
) -> ChatMessage | None:
    """Decide whether this empty turn earns a correction, and word it.

    Fired at most once, immediately after the first empty turn, and only when
    correcting is possible and warranted:

    * the run is not a resumed segment, whose earlier segments may already
      have delivered;
    * the task declares artifacts, so there is a concrete deliverable to name;
    * this is the first turn, so nothing has had a chance to deliver yet;
    * no tool has been called in any turn, so nothing was delivered;
    * a turn remains to correct in.

    A second empty turn falls through to the zero-artifact guard, so the
    correction costs one round trip and never loops.

    Args:
        ctx: The context of the run that produced an empty turn.
        turns: Every turn recorded so far, this one included.

    Returns:
        The corrective user message, or ``None`` when no nudge applies.
    """
    if is_resumed_run():
        return None
    execution = ctx.task_execution
    if execution is None or not execution.task.artifacts_expected:
        return None
    if len(turns) != 1 or any(t.tool_calls_made for t in turns):
        return None
    if ctx.max_turns <= 1:
        return None
    # Fenced, because a declared path is model-authored: decomposition takes
    # it from the LLM as a non-blank string with no charset restriction, so a
    # path spelling its own instructions would otherwise arrive inside a
    # sentence telling the agent to act on it now. The sibling that renders
    # the same field for the evaluate brief fences it the same way.
    declared = wrap_untrusted(
        TAG_TASK_DATA,
        ", ".join(artifact.path for artifact in execution.task.artifacts_expected),
    )
    return ChatMessage(
        role=MessageRole.USER,
        content=(
            "You answered without calling a single tool, so this task has "
            "produced nothing. It declares these deliverables: "
            f"{declared} Prose is not a deliverable. Use your tools to "
            "create them now, then say you are done."
        ),
    )


def nudge_empty_run(
    ctx: AgentContext,
    turns: list[TurnRecord],
    turn_number: int,
) -> AgentContext | None:
    """Extend *ctx* with a correction, when an empty turn earns one.

    Args:
        ctx: The context of the run that produced an empty turn.
        turns: Every turn recorded so far, this one included.
        turn_number: The 1-based number of the turn that came back empty.

    Returns:
        The context to run the corrective turn with, or ``None`` to let the
        caller treat the empty turn as the run's answer.
    """
    message = _nudge_message(ctx, turns)
    if message is None:
        return None
    logger.info(
        EXECUTION_LOOP_EMPTY_RUN_NUDGED,
        execution_id=ctx.execution_id,
        turn=turn_number,
        turns_remaining=ctx.max_turns - turn_number,
    )
    return ctx.with_message(message)
