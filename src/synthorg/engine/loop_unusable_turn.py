"""One corrective turn for a model that asked for a tool and delivered none.

Two shapes reach here and the correction names which one it saw, because a
model told the wrong thing repeats the same mistake. A streamed tool call
arrives as argument fragments the driver concatenates and parses; when the
model emits a malformed blob the accumulator refuses to guess and drops the
call. The other shape is a turn that ends as ``tool_use`` carrying no call at
all: nothing was dropped, the model simply sent none. Either leaves a
completion that says ``tool_use`` and carries nothing, or one that is empty on
every channel and was normalised to ``error`` on the way out of the driver.

Neither is the agent finishing and neither is the provider failing: it is the
model's own bad output, one turn of it. Ending the run there throws away every
productive turn before it, and it is not hypothetical -- a full A/B recording
lost 14 of 27 native-loop runs this way, at turn 2 or 3, while the bundled
harness (which parses its tool calls inside its own container) could not hit
it at all.

The correction fires a bounded number of times in a row, and any productive
turn resets the count. Past the bound the next unusable turn falls through, so
a provider returning nothing usable at all still ends the run rather than
looping. The bound and the count both live in :mod:`.loop_correction_budget`,
shared with :mod:`.loop_silent_turn`, because one budget per shape is no budget
at all against a model that alternates between them.
"""

from synthorg.core.completion_enums import FinishReason
from synthorg.engine.context import AgentContext
from synthorg.engine.failure_classification import UNUSABLE_OUTPUT_MARKER
from synthorg.engine.loop_correction_budget import (
    DROPPED_CALL_NUDGE,
    MAX_CONSECUTIVE_CORRECTIONS,
    NO_CALL_NUDGE,
    consecutive_corrections,
)
from synthorg.observability import get_logger
from synthorg.observability.events.execution import EXECUTION_LOOP_UNUSABLE_TURN
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage, CompletionResponse

logger = get_logger(__name__)


def unusable_turn_error(turn_number: int) -> str:
    """Build the run-ending error for a turn the corrections could not fix.

    Named rather than written inline at the one call site, because the phrase
    is also what classifies the failure: the rule matching
    ``UNUSABLE_OUTPUT_MARKER`` is the only thing keeping this out of
    ``UNKNOWN``, and a reword at the call site would move the category without
    touching the rule. One builder means the classifier can be asked about the
    exact string the loop produces.

    Args:
        turn_number: The turn that ended the run.

    Returns:
        The error message the loop reports.
    """
    return (
        f"Model returned {UNUSABLE_OUTPUT_MARKER} on turn "
        f"{turn_number} and the correction did not take"
    )


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
    consecutive = consecutive_corrections(ctx)
    # Reported whether or not it is corrected: a run that dies on an unusable
    # turn must say so by name, or the failure is attributed to the provider
    # rather than to the model's own output.
    corrected = (
        turn_number < ctx.max_turns and consecutive < MAX_CONSECUTIVE_CORRECTIONS
    )
    nudge = DROPPED_CALL_NUDGE if response.dropped_tool_calls else NO_CALL_NUDGE
    logger.warning(
        EXECUTION_LOOP_UNUSABLE_TURN,
        execution_id=ctx.execution_id,
        turn=turn_number,
        finish_reason=response.finish_reason.value,
        turns_remaining=ctx.max_turns - turn_number,
        consecutive_corrections=consecutive,
        corrected=corrected,
        # Which of the two shapes it was, so a run that spent its corrections
        # can be read back without guessing which one the model was told.
        cause="dropped_call" if response.dropped_tool_calls else "no_call",
    )
    if not corrected:
        return None
    return ctx.with_message(ChatMessage(role=MessageRole.USER, content=nudge))
