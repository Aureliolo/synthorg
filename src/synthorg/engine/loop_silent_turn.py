"""One corrective turn for a model that spent a whole turn thinking.

A reasoning model answers on two channels, and its visible one can come back
empty: the turn's token budget went entirely to reasoning. That is not the
agent finishing and not the provider failing, so ending the run there throws
away every turn before it. A live run died exactly this way on turn 48 of 50,
forty-seven productive turns discarded because the forty-eighth said nothing
out loud.

The correction fires at most once in a row: a second silent turn straight
after it falls through, so the run ends on its own guards rather than looping.
"""

from typing import Final

from synthorg.engine.context import AgentContext
from synthorg.observability import get_logger
from synthorg.observability.events.execution import EXECUTION_LOOP_SILENT_TURN
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, CompletionResponse

logger = get_logger(__name__)

SILENT_TURN_NUDGE: Final[str] = (
    "Your last turn produced no visible output: its whole token budget went "
    "to reasoning. Think briefly, then either call a tool or state your "
    "result in the reply itself."
)


def continue_silent_turn(
    ctx: AgentContext,
    response: CompletionResponse,
    turn_number: int,
) -> AgentContext | None:
    """Extend *ctx* with a correction when a turn produced only reasoning.

    Args:
        ctx: The context of the run that produced the silent turn.
        response: That turn's completion.
        turn_number: The 1-based number of the silent turn.

    Returns:
        The context to run the corrective turn with, or ``None`` when the
        turn was not silent, no turn remains, or the previous turn already
        earned this correction.
    """
    # Falsy, not ``is not None``: the streamed path joins to ``None`` but a
    # buffered one hands back the empty string the wire carried, and a turn
    # that said nothing is the same turn either way.
    if response.content or response.tool_calls:
        return None
    if response.reasoning is None:
        return None
    # Reported whether or not it is corrected: a run that dies on a silent
    # turn must say so, which is the whole reason this case was invisible.
    corrected = turn_number < ctx.max_turns and not _already_corrected(ctx)
    logger.warning(
        EXECUTION_LOOP_SILENT_TURN,
        execution_id=ctx.execution_id,
        turn=turn_number,
        reasoning_chars=len(response.reasoning),
        finish_reason=response.finish_reason.value,
        turns_remaining=ctx.max_turns - turn_number,
        corrected=corrected,
    )
    if not corrected:
        return None
    return ctx.with_message(
        ChatMessage(role=MessageRole.USER, content=SILENT_TURN_NUDGE)
    )


def _already_corrected(ctx: AgentContext) -> bool:
    """Report whether the correction is already the most recent user message.

    The nudge is its own bound: finding it immediately before this turn's
    empty assistant message means the previous turn was silent too.

    Returns:
        ``True`` when this run's last user message is the correction.
    """
    for message in reversed(ctx.conversation):
        if message.role is MessageRole.USER:
            return message.content == SILENT_TURN_NUDGE
    return False
