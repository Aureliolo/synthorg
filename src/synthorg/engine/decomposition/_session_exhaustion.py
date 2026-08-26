# module-kind: code
"""Which exhausted planning session raises which error.

A session that ran and never submitted a plan ended one of five ways, and they
are not the same failure. Three are the session reaching a bound of its own
(turns, tokens, a loop that stopped progressing): the level that asked for it
still holds a valid plan, so those are typed apart and the parent records the
unit rather than discarding every level already paid for.

The other two are the session finishing on its own terms with its one job
undone, which is a fault rather than a bound. Nothing is exhausted, the next
attempt is a fresh roll of the dice, and the retry ladder above is exactly the
right response, so they keep the base error.

Reached only once the session has nothing left to try; an agent that stops
early while it can still deliver is told so and continues.
"""

from typing import Final, NoReturn

from synthorg.engine.errors import (
    DecompositionError,
    DecompositionSessionBudgetError,
    DecompositionStagnationError,
    DecompositionTurnBudgetError,
)
from synthorg.engine.loop_protocol import TerminationReason

#: The bound each exhausting termination reached. A mapping rather than a
#: ``match``: every termination absent from it keeps the base error, which is
#: the safe direction (a retry that buys nothing costs one attempt, while
#: absorbing a real fault files an outage as a note on one plan item).
_EXHAUSTED: Final[dict[TerminationReason, type[DecompositionError]]] = {
    TerminationReason.MAX_TURNS: DecompositionTurnBudgetError,
    TerminationReason.BUDGET_EXHAUSTED: DecompositionSessionBudgetError,
    TerminationReason.STAGNATION: DecompositionStagnationError,
}


def raise_session_exhaustion(
    reason: TerminationReason,
    message: str,
) -> NoReturn:
    """Raise the right error for a session that submitted no plan.

    Args:
        reason: How the session terminated.
        message: What to raise, already scrubbed.

    Raises:
        DecompositionTurnBudgetError: The session used every turn.
        DecompositionSessionBudgetError: The session spent its token budget.
        DecompositionStagnationError: The session stopped progressing.
        DecompositionError: The session finished on its own terms with its one
            job undone, which is a fault rather than a bound.
    """
    raise _EXHAUSTED.get(reason, DecompositionError)(message)


__all__ = ["raise_session_exhaustion"]
