"""Bounded re-runs for a run the review gate sent back.

A completion review can return REWORK: the build/test oracle found no evidence
the work builds, or a peer reviewer rejected it. That verdict means "run this
again", and until now the only thing it did was write ``IN_PROGRESS`` onto the
task.

Nothing drives a task except a coordination wave, and the wave that ran this
one has already returned by the time the review lands. So every reworked task
sat ``IN_PROGRESS`` with no loop behind it and nothing watching: a live run put
all five items of a plan into that state at once, and the plan could never
finish. The gate was not wrong to say rework; it had nowhere to say it to.

The owner is the agent engine that ran the task, because it is the only thing
holding a loop that can continue. The correction fires there, in the same
dispatch, with the gate's own reason handed back as a user message, exactly as
:mod:`synthorg.engine.loop_unusable_turn` and
:mod:`synthorg.engine.loop_silent_turn` correct a wasted turn.

Bounded, because a model that ignored the reason twice will ignore it a third
time, and each round costs a full run. Past the bound the run stops being
reworked and fails, which is re-runnable and visible, rather than resting in a
status nothing watches.
"""

from typing import Final

from synthorg.engine.context import AgentContext
from synthorg.observability import get_logger
from synthorg.observability.events.execution import EXECUTION_LOOP_REWORK
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import ChatMessage

logger = get_logger(__name__)

#: ``ExecutionResult.metadata`` key carrying the reason a review sent the run
#: back. Read by the dispatch that ran it, which is the only party still
#: holding a loop it can continue.
REWORK_METADATA_KEY: Final[str] = "rework_reason"

#: Consecutive rework rounds a single dispatch will take before it fails. Each
#: round is a whole run, so this is a spend bound as much as a progress one: a
#: model that has been told twice why its work was refused and returned it
#: unchanged is not going to be told a third time to any effect.
MAX_REWORK_ROUNDS: Final[int] = 2

#: Handed to the agent as its next user turn. The gate's reason is quoted
#: rather than paraphrased: it is the only thing that says what would satisfy
#: the gate, and a paraphrase is how "no test run" becomes "try harder".
REWORK_NUDGE: Final[str] = (
    "Your work was reviewed and sent back rather than accepted.\n\n"
    "The reviewer said: {reason}\n\n"
    "Address that specifically, then finish. If the reason is that no test "
    "run was recorded, actually run the tests with the tools you have; a "
    "claim that they pass is not evidence that they do."
)

#: Recorded on the run when the bound is spent, so the task fails saying which
#: refusal it could not clear rather than merely that it stopped.
REWORK_EXHAUSTED_REASON: Final[str] = (
    "Sent back by review {rounds} times without clearing it; last reason: {reason}"
)


def continue_rework(
    ctx: AgentContext,
    reason: str,
    *,
    rounds_taken: int,
    execution_id: str,
) -> AgentContext | None:
    """Extend *ctx* with the review's reason so the run can answer it.

    Args:
        ctx: The context the finished run ended on, so the agent keeps the work
            it already did rather than starting the task over.
        reason: The gate's own words for why it sent the work back.
        rounds_taken: Rework rounds this dispatch has already taken.
        execution_id: For the log.

    Returns:
        The context to re-run with, or ``None`` when the bound is spent.
    """
    corrected = rounds_taken < MAX_REWORK_ROUNDS
    # Reported either way: a dispatch that gave up on a rework must say so,
    # which is precisely what the bare status write never did.
    logger.warning(
        EXECUTION_LOOP_REWORK,
        execution_id=execution_id,
        rounds_taken=rounds_taken,
        max_rounds=MAX_REWORK_ROUNDS,
        corrected=corrected,
        reason=reason,
    )
    if not corrected:
        return None
    return ctx.with_message(
        ChatMessage(role=MessageRole.USER, content=REWORK_NUDGE.format(reason=reason))
    )
