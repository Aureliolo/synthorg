# module-kind: code
"""Whether a charter draft may stand, or the human is asked about it first.

The interview decides when it has heard enough, and a live run showed what
that costs: one question, then a charter whose goals, success criteria,
scope, envelope and project were all supplied by the model and rendered
beside the one elicited answer with nothing to tell them apart. The operator
approves a scope they never agreed, and the charter then authorises an
initiative against it.

So the interview declares which facets it is filling itself, and this module
decides what happens next. The declaration is an input; the decision has one
owner and it is not the model.

Pressed exactly once per interview. The human may have nothing to add (or may
want the org to decide), and asking again would be a loop with no exit; what
survives the press is recorded on the charter instead, so an assumption the
human declined to settle is visible where they approve it rather than silent.
"""

from typing import Final

from synthorg.communication.conversation.enums import ConversationRole
from synthorg.core.types import NotBlankStr
from synthorg.meta.charter.enums import CharterFacet
from synthorg.meta.chief_of_staff.models import ConversationTurn

#: Opens every coverage press, and nothing else this module writes. A prior
#: assistant turn starting with it IS the record that the press already
#: happened, which is why it is a constant rather than phrasing.
COVERAGE_PRESS_LEAD_IN: Final[str] = (
    "Before I draft the charter, here is what I would otherwise decide for you:"
)

_CLOSING: Final[str] = (
    'Answer whichever of those you have a view on. Say "you decide" for the '
    "rest and I will note them on the charter as the org's assumption rather "
    "than your answer."
)

#: What to ask for each facet. Phrased as the thing the human knows rather
#: than the field name, because the field name is our vocabulary.
_FACET_ASKS: Final[dict[CharterFacet, str]] = {
    CharterFacet.GOALS: "what a finished version looks like to you",
    CharterFacet.CONSTRAINTS: "anything the work must or must not do",
    CharterFacet.SUCCESS_CRITERIA: (
        "how you want to be able to tell it is done, since that is what the "
        "finished work gets judged against"
    ),
    CharterFacet.SCOPE: "anything you want deliberately left out",
    CharterFacet.ENVELOPE: "how much this is worth spending, and by when",
    CharterFacet.PROJECT: "which project this belongs under, or a name for a new one",
}


def press_already_made(turns: tuple[ConversationTurn, ...]) -> bool:
    """Whether this interview has already been pressed on its assumptions.

    Args:
        turns: The conversation so far, in any order.

    Returns:
        Whether a prior assistant turn is a coverage press.
    """
    return any(
        turn.role is ConversationRole.ASSISTANT
        and turn.content.startswith(COVERAGE_PRESS_LEAD_IN)
        for turn in turns
    )


def coverage_question(assumed: tuple[CharterFacet, ...]) -> NotBlankStr:
    """Build the one question that puts *assumed* back to the human.

    Args:
        assumed: The facets the draft filled from its own judgement, in
            declaration order.

    Returns:
        The question to ask instead of drafting.
    """
    asks = "\n".join(f"- {_FACET_ASKS[facet]}" for facet in _ordered(assumed))
    return NotBlankStr(f"{COVERAGE_PRESS_LEAD_IN}\n\n{asks}\n\n{_CLOSING}")


def _ordered(assumed: tuple[CharterFacet, ...]) -> tuple[CharterFacet, ...]:
    """Deduplicate *assumed* while keeping a stable, declared order.

    Returns:
        Each facet once, in the order :class:`CharterFacet` declares them,
        so two drafts naming the same facets ask the same question.
    """
    named = set(assumed)
    return tuple(facet for facet in CharterFacet if facet in named)


__all__ = [
    "COVERAGE_PRESS_LEAD_IN",
    "coverage_question",
    "press_already_made",
]
