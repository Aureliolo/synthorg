# module-kind: code
"""Does a claim name one of the objective's criteria, and which one.

``satisfies`` carries criterion TEXT the planner copies out of the objective,
so "is this the same criterion" is a string question, and it is asked in four
places: the two boundaries that WRITE a claim (the planner's parse and the
operator's plan edit), the recursion that narrows an objective's criteria for
the level below, and the dashboard's coverage map. One answer for all four,
because four copies of it are four chances to disagree, and a disagreement
between any two of them reads exactly like a claim naming nothing.

Matching is forgiving about SPELLING and unforgiving about CONTENT. A model
copying a ninety-character sentence gets the capital and the spacing wrong, not
the sentence, so refusing a plan over a trailing space costs the planning call
and buys nothing. Inventing a sentence the objective never states is the
defect, and nothing here forgives it.

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


def unique_criteria(criteria: Iterable[str]) -> tuple[NotBlankStr, ...]:
    """Build a claim vocabulary, keeping one spelling per criterion.

    The single owner of "how does an objective's acceptance criteria become a
    vocabulary", because two entries with the same key are ONE criterion by
    this module's own definition of sameness, and carrying both makes it
    countable twice everywhere downstream: one claim matches both, the
    coverage map lists it twice, and a child level inherits a vocabulary of
    two that admits exactly one sentence.

    The FIRST spelling wins, so the vocabulary reads as the objective's author
    wrote it rather than as whichever duplicate happened to sort last.

    Args:
        criteria: The objective's acceptance criteria, as authored.

    Returns:
        The criteria in the order given, blank ones dropped, each key once.
    """
    seen: set[str] = set()
    vocabulary: list[NotBlankStr] = []
    for criterion in criteria:
        key = criterion_key(criterion)
        if not key or key in seen:
            continue
        seen.add(key)
        vocabulary.append(NotBlankStr(criterion))
    return tuple(vocabulary)


def matched_criteria(
    claims: Iterable[str], *, objective: Sequence[NotBlankStr]
) -> tuple[NotBlankStr, ...]:
    """The objective criteria at least one of *claims* names.

    Answers one entry per MATCHING objective entry, so a vocabulary carrying
    the same criterion twice answers twice. :func:`unique_criteria` is what
    keeps that from arising, since deduplicating here would leave the
    double-counted entry in the coverage list this is read beside.

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
        refusal quotes back what its author can search for. An empty
        *objective* states nothing, so every claim is unmatched: a level
        answerable for no criterion is one where any claim names nothing.
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

    An empty *objective* is not an exemption, it is the strictest case: a level
    answerable for nothing admits no claim at all. Skipping it instead left a
    whole subtree unchecked, because a pure-support unit is judged oversized on
    its artifact count with ``satisfies`` never entering the decision, so its
    descendants planned against an empty vocabulary and could claim anything.
    Refusing here is also what keeps every level's vocabulary a subset of the
    root's, which is the invariant the operator's edit boundary relies on to
    never refuse an item decomposition produced.

    One message per offending unit, because a session that regenerates its
    whole plan on each rejection cannot converge while it is told one violation
    at a time. The criteria to copy from are stated ONCE, at the end: repeating
    them per unit multiplies one list by the item count, and a plan may carry a
    thousand items against a hundred criteria.

    Args:
        units: The units as submitted.
        objective: The criteria the level is answerable for. Empty admits no
            claim, which is what an objective declaring none and a parent
            claiming none both amount to.

    Returns:
        A message per offending unit in submission order, followed by one
        naming what may be claimed; empty when every claim names something.
    """
    messages = [
        f"{unit.title!r} claims {', '.join(repr(claim) for claim in invented)}, "
        f"which the objective does not state, so nothing can tell what it "
        f"advances."
        for unit in units
        if (invented := unmatched_claims(unit.satisfies, objective=objective))
    ]
    if not messages:
        return ()
    if not objective:
        nothing_to_claim = (
            "This level is answerable for no objective criterion, so no item "
            "here may claim one: either the objective declared none, or the "
            "unit this level decomposes claimed none."
        )
        return (*messages, nothing_to_claim)
    stated = ", ".join(repr(criterion) for criterion in objective)
    return (
        *messages,
        f"Copy the criteria an item advances verbatim from: {stated}",
    )


__all__ = [
    "ClaimingUnit",
    "criterion_key",
    "describe_unnamed_claims",
    "matched_criteria",
    "unique_criteria",
    "unmatched_claims",
]
