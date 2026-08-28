"""Corrective turns for a model that spent a whole turn thinking.

A reasoning model answers on two channels, and its visible one can come back
empty: the turn's token budget went entirely to reasoning. That is not the
agent finishing and not the provider failing, so ending the run there throws
away every productive turn before it because the latest one said nothing out
loud.

The correction fires a bounded number of times in a row
(:data:`MAX_CONSECUTIVE_CORRECTIONS`), and any productive turn resets the
count. One in a row was the first bound and it was not enough: a merge agent
went silent on turns 7 and 8 of a budget of forty, the second turn had no
correction left, and the run ended there having read its inputs and written
nothing. Its sibling :mod:`.loop_unusable_turn` measured the same thing about
its own single correction and carries the same bound for the same reason.

Past the bound the run ends BY NAME, which is the other half of the same
defect. Falling through instead reaches the ordinary completion path, where a
task expecting artifacts that has not written one is reported as a silent
no-op with the note that the agent "finished leaving its workspace exactly as
it found it". It did not finish: it emitted consecutive turns this loop could
not act on, with most of its budget unspent, and a harness reading that verdict
records the work as having produced nothing rather than the run as having been
ended on the model's output shape.
"""

from typing import Final

from synthorg.engine.context import AgentContext
from synthorg.engine.failure_classification import UNUSABLE_OUTPUT_MARKER
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

#: Matched to :data:`.loop_unusable_turn.MAX_CONSECUTIVE_CORRECTIONS`, because
#: the two bound the same thing: how many turns of the model's own bad output a
#: run absorbs before it is the run's problem. Consecutive, so any productive
#: turn resets it, and small, so a model that says nothing at all still ends the
#: run well inside its turn budget rather than spending the whole thing.
MAX_CONSECUTIVE_CORRECTIONS: Final[int] = 3


def is_silent_turn(response: CompletionResponse) -> bool:
    """Report whether a completion spent its whole turn on reasoning.

    Args:
        response: The turn's completion.

    Returns:
        ``True`` when the turn carries reasoning and nothing the loop can act
        on.
    """
    # Falsy, not ``is not None``: the streamed path joins to ``None`` but a
    # buffered one hands back the empty string the wire carried, and a turn
    # that said nothing is the same turn either way.
    if response.content or response.tool_calls:
        return False
    return response.reasoning is not None


def silent_turn_error(turn_number: int) -> str:
    """Build the run-ending error for silence the corrections could not fix.

    Carries :data:`UNUSABLE_OUTPUT_MARKER` for the same reason its sibling
    does: that phrase is what classifies the failure, so a run ended here is
    attributed to the model's own output rather than landing in ``UNKNOWN``.

    Args:
        turn_number: The turn that ended the run.

    Returns:
        The error message the loop reports.
    """
    return (
        f"Model returned {UNUSABLE_OUTPUT_MARKER} on turn {turn_number}: "
        f"reasoning only, with no reply and no tool call, and the correction "
        f"did not take"
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
        turn was not silent, no turn remains, or the run has spent its
        consecutive corrections.
    """
    if not is_silent_turn(response) or response.reasoning is None:
        return None
    consecutive = _consecutive_corrections(ctx)
    # Reported whether or not it is corrected: a run that dies on a silent
    # turn must say so, which is the whole reason this case was invisible.
    corrected = (
        turn_number < ctx.max_turns and consecutive < MAX_CONSECUTIVE_CORRECTIONS
    )
    logger.warning(
        EXECUTION_LOOP_SILENT_TURN,
        execution_id=ctx.execution_id,
        turn=turn_number,
        reasoning_chars=len(response.reasoning),
        finish_reason=response.finish_reason.value,
        turns_remaining=ctx.max_turns - turn_number,
        consecutive_corrections=consecutive,
        corrected=corrected,
    )
    if not corrected:
        return None
    return ctx.with_message(
        ChatMessage(role=MessageRole.USER, content=SILENT_TURN_NUDGE)
    )


def _consecutive_corrections(ctx: AgentContext) -> int:
    """Count the corrections issued since the last productive turn.

    Walks the tail of the conversation, counting nudges and stepping over the
    silent assistant turn each one answers, and stops at anything else. A
    productive turn ends in a tool call and its result, so both stop the walk;
    skipping every non-user message instead would read past them and count a
    nudge from earlier in the run as consecutive with a later one, spending
    the bound across unrelated stumbles rather than one stuck stretch.

    Returns:
        The number of consecutive corrections at the end of the run.
    """
    consecutive = 0
    for message in reversed(ctx.conversation):
        if message.role is MessageRole.USER:
            if message.content != SILENT_TURN_NUDGE:
                break
            consecutive += 1
            continue
        if message.role is MessageRole.ASSISTANT and not message.tool_calls:
            # The silent turn the nudge above answers. Empty on the visible
            # channel by definition, so the discriminator is the absent call.
            continue
        break
    return consecutive
