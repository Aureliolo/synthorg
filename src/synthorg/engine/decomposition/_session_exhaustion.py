# module-kind: code
"""What a termination reason means for a planning session.

Three separate decisions read the way a session ended, and each is a safety
decision taken per termination rather than a membership test: may the
single-shot fallback stand in, may the agent be handed another turn, and which
error the exhaustion raises. They live together because they are the same
question asked three ways, and because a newly-added
:class:`TerminationReason` has to be classified in all three.

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

from typing import Final, NoReturn, assert_never

from synthorg.engine.errors import (
    DecompositionError,
    DecompositionSessionBudgetError,
    DecompositionStagnationError,
    DecompositionTurnBudgetError,
)
from synthorg.engine.loop_protocol import TerminationReason


def ran_without_submitting(reason: TerminationReason) -> bool:
    """Report whether a verdict-less session had a researched plan to lose.

    Substituting a single-shot plan for a session that ran on its own terms
    hands the operator a different plan than the one they asked for,
    indistinguishable from the real thing at the approval gate. Where the
    session could not run at all, nothing was lost and the fallback stands.

    A ``match`` with :func:`assert_never` rather than a membership set: the
    fallback is a safety decision per termination, so a newly-added
    :class:`TerminationReason` must be classified deliberately, and this
    makes omitting it a type error rather than a silent grant of the
    fallback.

    Returns:
        ``True`` when the session ran and produced nothing.
    """
    match reason:
        case (
            TerminationReason.COMPLETED
            | TerminationReason.NO_OP
            | TerminationReason.MAX_TURNS
            | TerminationReason.BUDGET_EXHAUSTED
            | TerminationReason.STAGNATION
        ):
            return True
        # ERROR never reached the model, SHUTDOWN lost the process under it,
        # and PARKED / CANCELLED stopped the session by a decision taken
        # outside it (an approval wait, an operator abort): in all four the
        # session was prevented from producing rather than producing nothing.
        case (
            TerminationReason.ERROR
            | TerminationReason.SHUTDOWN
            | TerminationReason.PARKED
            | TerminationReason.CANCELLED
        ):
            return False
        case _ as unreachable:
            assert_never(unreachable)


def stopped_short(reason: TerminationReason) -> bool:
    """Report whether the session ended its own turn with its work undone.

    A planning session has exactly one deliverable, and the tools it delivers
    through hand a rejection straight back, so an agent that ends its turn
    holding a rejected plan is in the ordinary state of any coding loop: told
    what is wrong, with turns left to fix it. Ending the session there is what
    turned a punctuation rejection into a dead run; the answer is the same one
    a coding agent gets, which is to be told it has not delivered and to carry
    on.

    Separate from :func:`ran_without_submitting`, which answers a different
    question (was there a researched plan to lose), and the same ``match`` with
    :func:`assert_never` for the same reason: continuing to spend an agent's
    turns is a decision per termination, so a new :class:`TerminationReason`
    must be classified deliberately.

    Returns:
        ``True`` when the session stopped on its own while it could still
        deliver.
    """
    match reason:
        case TerminationReason.COMPLETED | TerminationReason.NO_OP:
            return True
        # MAX_TURNS and BUDGET_EXHAUSTED are the bounds themselves, so another
        # turn is exactly what they refuse; STAGNATION means the loop is
        # already repeating itself, and re-prompting is one more repetition.
        # ERROR is the loop giving up rather than the agent doing so, whether
        # the provider failed or the corrections for unusable turns ran out;
        # either way the next turn fails the same way. SHUTDOWN, PARKED and
        # CANCELLED stopped the session from outside it, and nothing here can
        # hand it back.
        case (
            TerminationReason.MAX_TURNS
            | TerminationReason.BUDGET_EXHAUSTED
            | TerminationReason.STAGNATION
            | TerminationReason.ERROR
            | TerminationReason.SHUTDOWN
            | TerminationReason.PARKED
            | TerminationReason.CANCELLED
        ):
            return False
        case _ as unreachable:
            assert_never(unreachable)


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


__all__ = [
    "raise_session_exhaustion",
    "ran_without_submitting",
    "stopped_short",
]
