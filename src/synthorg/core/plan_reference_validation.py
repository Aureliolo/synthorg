# module-kind: code
"""An item naming another the plan does not order it against.

Split from :mod:`.plan_validation` for the same reason
:mod:`.plan_tree_validation` was: that module holds every plan-unit invariant
every boundary shares, and the two that read one item's PROSE against another's
are a distinct question with their own vocabulary rules, their own per-plan
index and their own tuning constants.

The question is when a title or description reads as a reference. Everything
that follows is about telling a reference from the vocabulary a plan happens to
repeat, which is why the constants below are here rather than beside the graph
invariants.
"""

from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

from synthorg.core.plan_validation import PlanUnit

#: Words too common to constitute a reference to another item. A title token
#: only implicates another item when it names that item's subject, and every
#: plan is full of these.
_GENERIC_TITLE_TOKENS: Final[frozenset[str]] = frozenset(
    {
        "a",
        "add",
        "an",
        "and",
        "build",
        "create",
        "for",
        "implement",
        "in",
        "of",
        "on",
        "set",
        "setup",
        "the",
        "to",
        "up",
        "with",
        "write",
    }
)

#: Shortest token that can identify another item's subject. Anything shorter
#: is a preposition or an abbreviation shared across unrelated items.
_MIN_REFERENCE_TOKEN: Final[int] = 4

#: Largest share of a plan's titles a token may appear in and still name one
#: item; the bound is inclusive, so a token in exactly half the titles is kept.
#: A word carried by MORE than half the plan is that plan's house vocabulary
#: ("Subtask 1" / "Subtask 2", "Service layer" / "Service tests"), and treating
#: it as a reference would make every item depend on every other.
_MAX_DISTINCTIVE_SHARE: Final[float] = 0.5


def _words(text: str) -> list[str]:
    """Split *text* into lowercased alphanumeric words.

    Returns:
        The words, in order, with punctuation dropped.
    """
    return "".join(c if c.isalnum() else " " for c in text.lower()).split()


def _title_tokens(unit: PlanUnit) -> frozenset[str]:
    """Reduce a unit's title to the tokens that could name its subject.

    Returns:
        Lowercased alphanumeric title tokens, minus the generic verbs and
        articles every plan title carries.
    """
    return frozenset(
        word
        for word in _words(unit.title)
        if len(word) >= _MIN_REFERENCE_TOKEN and word not in _GENERIC_TITLE_TOKENS
    )


@dataclass(frozen=True, slots=True)
class _ReferenceIndex:
    """Every token fact the reference scan reads, derived once per plan.

    Both halves of a comparison are properties of the PLAN rather than of the
    pair being compared: whether one of an item's title tokens is distinctive
    depends on how many of the plan's titles carry it, and an item's own words
    do not change between comparisons. Deriving them inside the pairwise loop
    made the scan grow super-quadratically in the item count, so the
    hand-authored edit boundary could be handed a list that never returns.

    Both tuples are positionally aligned with the units they were built from
    rather than keyed by id, because this runs on payloads that have not yet
    been checked for unique ids and a map would silently drop a duplicate.
    """

    text: tuple[frozenset[str], ...]
    distinctive: tuple[frozenset[str], ...]


def _reference_index(units: Sequence[PlanUnit]) -> _ReferenceIndex:
    """Derive the reference-scan facts for *units*.

    A token carried by more than half the plan's titles is that plan's house
    vocabulary, not a reference: matching on it made "Subtask 2" a reference
    to "Subtask 1", and would make every item in a plan named after one
    subject depend on every other.

    Returns:
        Per-unit words and distinctive title tokens, aligned with *units*.
    """
    titles = tuple(_title_tokens(unit) for unit in units)
    carrying = Counter(token for tokens in titles for token in tokens)
    ceiling = len(titles) * _MAX_DISTINCTIVE_SHARE
    return _ReferenceIndex(
        text=tuple(
            frozenset(_words(f"{unit.title} {unit.description}")) for unit in units
        ),
        distinctive=tuple(
            frozenset(token for token in tokens if carrying[token] <= ceiling)
            for tokens in titles
        ),
    )


def _ordered(unit: PlanUnit, other: PlanUnit) -> bool:
    """Whether the plan already orders *unit* against *other*, either way.

    The hazard this module reports is a reference that leaves the referring
    item free to be dispatched FIRST. An edge in either direction removes it:
    ``unit`` depending on ``other`` is the obvious case, and ``other``
    depending on ``unit`` puts ``unit`` first by construction, which is what a
    forward reference ("emits the tokens the parser consumes") describes.

    Reading only the first direction turned a lexer that mentioned its parser
    into a demand for a lexer-depends-on-parser edge, closing a cycle the
    validator rejects on the next submission: a plan that could be corrected
    only by rewording, told to add a dependency instead, until the retries ran
    out.

    Returns:
        True when either item declares a dependency on the other.
    """
    return other.id in unit.dependencies or unit.id in other.dependencies


def _unstated_reference(
    *,
    units: Sequence[PlanUnit],
    index: _ReferenceIndex,
    position: int,
) -> str | None:
    """Describe the first unstated reference the unit at *position* makes.

    Self-comparison is skipped by ID rather than by position, because the
    singular caller may hand its own unit in twice: once as the subject and
    again inside *units*, which the plural caller always does.

    Returns:
        A message naming both items, or ``None`` when the unit names nothing
        it is not already ordered against.
    """
    unit = units[position]
    text = index.text[position]
    for other_position, other in enumerate(units):
        if other.id == unit.id or _ordered(unit, other):
            continue
        tokens = index.distinctive[other_position]
        if tokens and tokens <= text:
            return (
                f"{unit.id!r} names {other.id!r} ({other.title!r}) in its own "
                "title or description but declares no dependency on it, so it "
                "may be dispatched first. Declare the dependency, or reword it "
                "if the items are genuinely independent"
            )
    return None


def describe_unstated_reference(
    *,
    unit: PlanUnit,
    others: Sequence[PlanUnit],
) -> str | None:
    """Describe an item naming another the plan does not order it against.

    "Integrate game loop: tie engine, renderer, and input together" cannot
    precede the three items it names, but with no declared dependency the
    dispatcher is free to run it first, and did. An edge in either direction
    settles the order and clears the reference; see :func:`_ordered`.

    Matching is on whole words, and only on the other item's DISTINCTIVE title
    tokens, so it fires on a genuine reference rather than on the vocabulary a
    plan happens to repeat.

    Args:
        unit: The unit being checked.
        others: Every other unit in the plan.

    Returns:
        A message naming both items, or ``None`` when no unstated reference
        is found.
    """
    scanned = (unit, *others)
    return _unstated_reference(
        units=scanned, index=_reference_index(scanned), position=0
    )


def describe_unstated_references(units: Sequence[PlanUnit]) -> tuple[str, ...]:
    """Describe every item naming another it does not depend on.

    One message per offending unit, because the plural caller's job is to hand
    the planner a complete list rather than the first thing that went wrong.

    The index is built once for the whole plan and read by every comparison;
    see :class:`_ReferenceIndex` for why that is not an optimisation detail.

    Returns:
        A message per offending unit, in plan order; empty when the graph is
        clean.
    """
    index = _reference_index(units)
    return tuple(
        message
        for position in range(len(units))
        if (message := _unstated_reference(units=units, index=index, position=position))
        is not None
    )


__all__ = ["describe_unstated_reference", "describe_unstated_references"]
