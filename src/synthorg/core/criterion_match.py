# module-kind: code
"""Does a claim name one of the objective's criteria, and which one.

``satisfies`` carries criterion TEXT the planner copies out of the objective,
so "is this the same criterion" is a string question, and it is asked in four
places: the two boundaries that WRITE a claim (the planner's parse and the
operator's plan edit), the recursion that narrows an objective's criteria for
the level below, and the dashboard's coverage map. Four copies of that answer
is four chances to disagree, and disagreeing is what a claim naming nothing
looked like for a whole recorded sweep.

Matching is forgiving about SPELLING and unforgiving about CONTENT. A model
copying a ninety-character sentence gets the capital and the spacing wrong, not
the sentence, and refusing a plan over a trailing space is how a style rule once
cost 18 of 25 planning calls. Inventing a sentence the objective never states is
the defect, and nothing here forgives it.

:func:`matched_criteria` answers with the OBJECTIVE's own text rather than the
claim's, which is the property recursion rests on: the level below is handed
the criteria its parent claimed, and handing it the claim's spelling instead
would move the vocabulary one normalisation step per level until nothing
matched at all.
"""

from collections.abc import Iterable, Sequence
from typing import Protocol

from synthorg.core.normalization import collapse_whitespace_lowercase
from synthorg.core.types import NotBlankStr


class ClaimingUnit(Protocol):
    """What the refusal reads off one unit that claims.

    Structural for the same reason :class:`~synthorg.core.plan_validation.PlanUnit`
    is: the durable plan item and the decomposition subtask both satisfy it,
    and neither module may import the other.
    """

    @property
    def title(self) -> str:
        """Human title of the unit, which is what the refusal names it by."""
        ...

    @property
    def satisfies(self) -> tuple[str, ...]:
        """The objective criteria this unit claims to advance."""
        ...


def criterion_key(text: str) -> str:
    """Reduce a criterion to what decides whether two of them are the same.

    Args:
        text: A criterion, or a claim naming one.

    Returns:
        The comparison key: trimmed, lowercased, internal whitespace runs
        collapsed.
    """
    return collapse_whitespace_lowercase(text)


def matched_criteria(
    claims: Iterable[str], *, objective: Sequence[NotBlankStr]
) -> tuple[NotBlankStr, ...]:
    """The objective criteria at least one of *claims* names.

    Args:
        claims: What a unit declared it advances.
        objective: The criteria the level is answerable for.

    Returns:
        The matched criteria as the OBJECTIVE spells them, in objective order,
        each at most once.
    """
    claimed = {criterion_key(claim) for claim in claims}
    return tuple(
        criterion for criterion in objective if criterion_key(criterion) in claimed
    )


def unmatched_claims(
    claims: Iterable[str], *, objective: Sequence[NotBlankStr]
) -> tuple[str, ...]:
    """The claims naming no criterion the objective states.

    Args:
        claims: What a unit declared it advances.
        objective: The criteria the level is answerable for.

    Returns:
        The offending claims exactly as written, in the order written, so a
        refusal quotes back what its author can search for.
    """
    stated = {criterion_key(criterion) for criterion in objective}
    return tuple(claim for claim in claims if criterion_key(claim) not in stated)


def describe_unnamed_claims(
    units: Sequence[ClaimingUnit], *, objective: Sequence[NotBlankStr]
) -> tuple[str, ...]:
    """Describe every unit claiming something the objective does not state.

    Says nothing about a unit claiming NOTHING: a genuine pure-support item
    advances no objective criterion directly, and that is the field's own
    documented semantics. What has no reading is a claim naming a sentence the
    objective never states, which reads as coverage to every consumer and is
    coverage to none.

    One message per offending unit, because a session that regenerates its
    whole plan on each rejection cannot converge while it is told one violation
    at a time.

    Args:
        units: The units as submitted.
        objective: The criteria the level is answerable for. Empty skips the
            check: an objective declaring none has no coverage to claim, and
            neither has a subtree whose parent claimed none.

    Returns:
        A message per offending unit, in submission order; empty when every
        claim names something.
    """
    if not objective:
        return ()
    stated = ", ".join(repr(str(criterion)) for criterion in objective)
    messages: list[str] = []
    for unit in units:
        invented = unmatched_claims(unit.satisfies, objective=objective)
        if not invented:
            continue
        quoted = ", ".join(repr(claim) for claim in invented)
        messages.append(
            f"{unit.title!r} claims {quoted}, which the objective does not "
            f"state, so nothing can tell what it advances. Copy the criteria "
            f"it advances verbatim from: {stated}"
        )
    return tuple(messages)


__all__ = [
    "ClaimingUnit",
    "criterion_key",
    "describe_unnamed_claims",
    "matched_criteria",
    "unmatched_claims",
]
