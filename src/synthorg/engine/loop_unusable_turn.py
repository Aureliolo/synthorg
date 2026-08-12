"""One corrective turn for a model that asked for a tool and delivered none.

A streamed tool call arrives as argument fragments the driver concatenates and
parses. When the model emits a malformed blob the accumulator refuses to guess
and drops the call, which leaves a completion that says ``tool_use`` and
carries nothing, or one that is empty on every channel and was normalised to
``error`` on the way out of the driver.

Neither is the agent finishing and neither is the provider failing: it is the
model's own bad output, one turn of it. Ending the run there throws away every
productive turn before it, and it is not hypothetical -- a full A/B recording
lost 14 of 27 native-loop runs this way, at turn 2 or 3, while the bundled
harness (which parses its tool calls inside its own container) could not hit
it at all.

The correction fires a bounded number of times in a row
(:data:`MAX_CONSECUTIVE_CORRECTIONS`), and any productive turn resets the
count. Past the bound the next unusable turn falls through, so a provider
returning nothing usable at all still ends the run rather than looping.
"""

from typing import Final

from synthorg.core.completion_enums import FinishReason
from synthorg.engine.context import AgentContext
from synthorg.observability import get_logger
from synthorg.observability.events.execution import EXECUTION_LOOP_UNUSABLE_TURN
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, CompletionResponse

logger = get_logger(__name__)

UNUSABLE_TURN_NUDGE: Final[str] = (
    "Your last turn asked to call a tool but the call did not arrive: its "
    "arguments were not valid JSON. Re-issue it as one well-formed call with "
    "complete arguments, or state your result in the reply itself."
)

# A model that stumbles can recover, and one turn of grace was not enough to
# let it: correcting only once in a row still lost most of the runs the
# correction was written for. Consecutive, so any productive turn resets it,
# and small, so a provider returning nothing usable at all still ends the run
# well inside the turn budget rather than spending the whole thing.
MAX_CONSECUTIVE_CORRECTIONS: Final[int] = 3


def is_unusable_turn(response: CompletionResponse) -> bool:
    """Report whether a completion signalled tool use and delivered nothing.

    Two shapes reach here. A ``TOOL_USE`` finish with no surviving tool call is
    the driver having dropped an unparsable one. A turn empty on every channel
    arrives as ``ERROR``, because the driver normalises it there so the loop
    receives a well-formed response to recover from; a provider that genuinely
    failed says so in the content, and keeps its ``ERROR`` meaning.

    Args:
        response: The turn's completion.

    Returns:
        ``True`` when the turn produced nothing the loop can act on.
    """
    if response.tool_calls:
        return False
    if response.finish_reason is FinishReason.TOOL_USE:
        return True
    return (
        response.finish_reason is FinishReason.ERROR
        and not response.content
        and response.reasoning is None
    )


def continue_unusable_turn(
    ctx: AgentContext,
    response: CompletionResponse,
    turn_number: int,
) -> AgentContext | None:
    """Extend *ctx* with a correction when a turn delivered nothing usable.

    Args:
        ctx: The context of the run that produced the unusable turn.
        response: That turn's completion.
        turn_number: The 1-based number of the unusable turn.

    Returns:
        The context to run the corrective turn with, or ``None`` when the turn
        was usable, no turn remains, or the previous turn already earned this
        correction.
    """
    if not is_unusable_turn(response):
        return None
    consecutive = _consecutive_corrections(ctx)
    # Reported whether or not it is corrected: a run that dies on an unusable
    # turn must say so, which is the whole reason this case read as a provider
    # failure for as long as it did.
    corrected = (
        turn_number < ctx.max_turns and consecutive < MAX_CONSECUTIVE_CORRECTIONS
    )
    logger.warning(
        EXECUTION_LOOP_UNUSABLE_TURN,
        execution_id=ctx.execution_id,
        turn=turn_number,
        finish_reason=response.finish_reason.value,
        turns_remaining=ctx.max_turns - turn_number,
        consecutive_corrections=consecutive,
        corrected=corrected,
    )
    if not corrected:
        return None
    return ctx.with_message(
        ChatMessage(role=MessageRole.USER, content=UNUSABLE_TURN_NUDGE)
    )


def _consecutive_corrections(ctx: AgentContext) -> int:
    """Count the corrections issued since the last productive turn.

    Walks the user messages from the end and stops at the first that is not
    the nudge, so any turn the model spent usefully resets the budget: the
    bound is on a model stuck emitting nothing, not on one that stumbles.

    Returns:
        The number of consecutive corrections at the end of the run.
    """
    consecutive = 0
    for message in reversed(ctx.conversation):
        if message.role is not MessageRole.USER:
            continue
        if message.content != UNUSABLE_TURN_NUDGE:
            break
        consecutive += 1
    return consecutive
