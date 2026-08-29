"""The one budget bounding every corrective turn the loop issues.

Two shapes of unusable model output earn a correction: a turn that spent its
whole budget on reasoning (:mod:`.loop_silent_turn`) and a turn that claimed a
tool call and delivered none (:mod:`.loop_unusable_turn`). They are different
diagnoses and are told different things, so each owns its own wording; what
they must NOT own separately is how many corrections a stuck run absorbs.

A per-module count is one budget per shape, and a model alternating between the
shapes spends neither. Each walk stops at the first message it does not
recognise, so an unusable nudge ends the silent walk at zero and a silent nudge
ends the unusable one, and a run that never produces a usable turn is corrected
without bound while both counters read below the limit. The bound exists to end
such a run well inside its turn budget, which is exactly the run it failed to
end.

So the count is here, over both nudge sets at once, and the limit with it.
"""

from collections.abc import Sequence
from typing import Final

from synthorg.engine.context import AgentContext
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage

SILENT_TURN_NUDGE: Final[str] = (
    "Your last turn produced no visible output: its whole token budget went "
    "to reasoning. Think briefly, then either call a tool or state your "
    "result in the reply itself."
)

#: A call arrives and is dropped for three different reasons: it carried no
#: function at all, it named no tool or no id, or its arguments were not a
#: well-formed JSON object. Naming only the last would tell a model that sent
#: perfectly good arguments to go and fix them, which is the same unactionable
#: instruction the no-call wording below exists to avoid.
DROPPED_CALL_NUDGE: Final[str] = (
    "Your last turn asked to call a tool but the call did not arrive in a "
    "usable form: it was missing the name or id that identifies it, or its "
    "arguments were not a well-formed JSON object. Re-issue it as one "
    "well-formed call with complete arguments, or state your result in the "
    "reply itself."
)

#: The other way a turn claims a tool and delivers none: the provider sent no
#: call at all. Kept apart from the dropped-call wording, which would tell the
#: model to fix arguments it never sent and so describes nothing it can act on.
NO_CALL_NUDGE: Final[str] = (
    "Your last turn ended as a tool call but carried no call at all, so "
    "nothing ran. Send exactly one tool call now, or answer in the reply "
    "itself if you have what you need."
)

#: Every correction the loop issues, so the walk below recognises any of them
#: and a run cannot alternate shapes to escape the bound.
CORRECTION_NUDGES: Final[frozenset[str]] = frozenset(
    {SILENT_TURN_NUDGE, DROPPED_CALL_NUDGE, NO_CALL_NUDGE}
)

#: How many turns of the model's own bad output a run absorbs before it is the
#: run's problem. Consecutive, so any productive turn resets it, and small, so
#: a model returning nothing usable at all still ends the run well inside its
#: turn budget rather than spending the whole thing. One in a row was the first
#: bound and it was not enough: a merge agent went silent on turns 7 and 8 of a
#: budget of forty, the second turn had no correction left, and the run ended
#: there having read its inputs and written nothing.
MAX_CONSECUTIVE_CORRECTIONS: Final[int] = 3


def _walk_correction_tail(conversation: Sequence[ChatMessage]) -> tuple[int, int]:
    """Walk the trailing correction stretch, newest message first.

    Counts nudges of either shape and steps over the call-less assistant turn
    each one answers, stopping at anything else. A productive turn ends in a
    tool call and its result, so both stop the walk; skipping every non-user
    message instead would read past them and count a nudge from earlier in the
    run as consecutive with a later one, spending the bound across unrelated
    stumbles rather than one stuck stretch.

    Args:
        conversation: The messages to walk, oldest first.

    Returns:
        ``(nudges, messages)`` -- how many corrections the stretch carries, and
        how many trailing messages it occupies.
    """
    nudges = 0
    messages = 0
    for message in reversed(conversation):
        if message.role is MessageRole.USER:
            if message.content not in CORRECTION_NUDGES:
                break
            nudges += 1
            messages += 1
            continue
        if message.role is MessageRole.ASSISTANT and not message.tool_calls:
            # The turn the nudge above answers. It can carry text (a dropped
            # call typically follows a preamble) and is empty by definition
            # when it was silent, so the discriminator is the absent call.
            messages += 1
            continue
        break
    return nudges, messages


def consecutive_corrections(ctx: AgentContext) -> int:
    """Count the corrections issued since the last productive turn.

    Args:
        ctx: The context whose conversation tail is walked.

    Returns:
        The number of consecutive corrections at the end of the run.
    """
    nudges, _ = _walk_correction_tail(ctx.conversation)
    return nudges


def correction_tail_messages(conversation: Sequence[ChatMessage]) -> int:
    """How many trailing messages the current correction stretch occupies.

    The bound above is derived from the transcript, so anything that rewrites
    the transcript can spend it without meaning to. Compaction archives
    everything outside a preserved window and replaces it with a summary, and
    the window is a turn count an operator sets: at the default it happens to
    be the same size as a full correction stretch, and at the minimum it is
    two messages. Either way the walk restarts inside the preserved window,
    the count reads lower than the run has actually earned, and the model that
    the bound exists to stop is corrected past it.

    So compaction asks how long the stretch is and keeps it whole. It is
    bounded by ``MAX_CONSECUTIVE_CORRECTIONS`` nudges and the turns they
    answer, so it can never hold compaction back by more than a handful of
    messages, and a productive turn ends it immediately.

    Args:
        conversation: The messages to measure, oldest first.

    Returns:
        The number of trailing messages that must survive intact.
    """
    _, messages = _walk_correction_tail(conversation)
    return messages
