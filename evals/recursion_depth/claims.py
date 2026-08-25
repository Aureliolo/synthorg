# module-kind: code
"""Translate between a spec requirement id and the criterion text that carries it.

The root objective is filed with one acceptance criterion per requirement, and
the planner echoes those criterion STRINGS back on each subtask's ``satisfies``
(the product's field means "objective success criteria this subtask advances",
not "requirement ids"). Everything downstream wants the id: the brief looks the
requirement's prose up by id, and survival is scored by intersecting claims with
the ids the oracle reports passing.

Both directions live here because they are one fact. Split across the module
that mints the criterion and the modules that read it back, they drifted, and
nothing failed: the brief rendered "R01 is satisfied: no such requirement" for
every requirement a leaf was answerable for, so every executor worked from its
unit title alone; and the survival intersection compared criterion text against
bare ids, so the surviving count was zero in every cell of every run.

That mapping is still not sound, and the curve no longer rests on it: a live
sweep dropped 143 claims naming no requirement the specification defines, which
is why :mod:`evals.recursion_depth.score` divides by the specification rather
than by what the leaves claimed. The claims here still decide what a leaf is
told it is answerable for, so the brief half remains load-bearing.
"""

import re
from collections.abc import Iterable, Sequence
from typing import NamedTuple, NewType

from synthorg.observability import get_logger
from synthorg.observability.events.evals import EVALS_RECURSION_CLAIM_UNRESOLVED

logger = get_logger(__name__)

#: One token of the closed vocabulary the specification declares.
#:
#: Distinct from ``str`` so that the confusion this module exists to prevent is
#: a type error rather than a silent zero: criterion prose and a requirement id
#: are both text, they flow through the same tuples, and reading one as the
#: other cost a whole sweep. Every id enters through
#: :func:`evals.recursion_depth.oracle.requirement_ids` or
#: :func:`requirement_ids_of`, both of which read the spec's own declaration,
#: so a value of this type has been checked against the vocabulary.
RequirementId = NewType("RequirementId", str)

#: The criterion a requirement id is filed as on the root objective.
_CRITERION_TEMPLATE = "{identifier} is satisfied"

#: A requirement id anywhere in a criterion, so a planner that rewords the
#: text around the id still resolves. The id itself is never reworded: it is
#: the token the specification and the oracle both key on.
_IDENTIFIER = re.compile(r"\bR\d+\b")


class ResolvedClaims(NamedTuple):
    """What one unit's claims resolved to.

    Two fields rather than one, because the counts are in DIFFERENT UNITS and
    neither can be derived from the other: ``ids`` counts requirements, after
    deduplication, while ``unresolved`` counts CLAIMS. Subtracting the first
    from the claim count is wrong in both directions, and both are reachable
    from an ordinary planner: one claim naming two requirements makes the
    difference negative, which ``UnitRecord.unresolved_claims`` refuses at
    ``ge=0`` and so discards a cell every leaf of which was already paid for;
    two claims naming one requirement makes it over-report drift, which is the
    one signal the caveat exists to carry.

    Attributes:
        ids: The resolved ids, deduplicated, in the order first seen.
        unresolved: How many claims named no requirement the spec defines.
    """

    ids: tuple[RequirementId, ...]
    unresolved: int


def criterion_for(identifier: RequirementId) -> str:
    """Render the acceptance criterion carrying *identifier*.

    Returns:
        The criterion text filed on the root objective.
    """
    return _CRITERION_TEMPLATE.format(identifier=identifier)


def requirement_ids_of(
    claims: Iterable[str], *, known: Sequence[RequirementId], unit: str
) -> ResolvedClaims:
    """Resolve *claims* to the spec requirement ids they name.

    A claim resolves on the id token it contains, checked against *known*, so
    a planner that rewords the criterion still scores and one that invents a
    requirement does not. An unresolvable claim is dropped with a WARNING
    naming it rather than passed through: passed through it reaches the brief
    as "no such requirement" and the survival intersection as a string that
    matches nothing, both of which read as an ordinary zero.

    Args:
        claims: What the planner declared for one unit.
        known: Every requirement id the specification defines.
        unit: The unit the claims belong to, for the warning.

    Returns:
        The resolved ids and how many claims resolved to nothing. The count is
        taken HERE, where a claim is still one claim, rather than derived by a
        caller from the two lengths: those are different units and the
        subtraction is wrong whenever a claim names two requirements or two
        claims name one.
    """
    vocabulary = frozenset(known)
    resolved: list[RequirementId] = []
    # Carried beside the list rather than testing membership against it. The
    # list would answer the same question, but only while every append happens
    # before the next test, which is a property of how the appends are written
    # rather than of what the function promises: `extend` fed a generator
    # interleaves the two, `extend` fed a list does not, and the difference is
    # invisible at the call site. A claim that names one requirement twice is
    # what separates them, and the set makes the answer independent of that.
    seen: set[RequirementId] = set()
    unresolved = 0
    for claim in claims:
        found = [
            RequirementId(one)
            for one in _IDENTIFIER.findall(claim)
            if one in vocabulary
        ]
        if not found:
            logger.warning(EVALS_RECURSION_CLAIM_UNRESOLVED, unit=unit, claim=claim)
            unresolved += 1
            continue
        for identifier in found:
            if identifier not in seen:
                seen.add(identifier)
                resolved.append(identifier)
    return ResolvedClaims(ids=tuple(resolved), unresolved=unresolved)


__all__ = [
    "RequirementId",
    "ResolvedClaims",
    "criterion_for",
    "requirement_ids_of",
]
