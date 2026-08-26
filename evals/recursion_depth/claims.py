# module-kind: code
"""Translate between a spec requirement id and the criterion text that carries it.

The root objective is filed with one acceptance criterion per requirement, and
every level of the plan claims from that same list: the product narrows an
objective's criteria to what a unit claimed before planning the level below it,
so a claim made at any depth still names a criterion this module minted.
Everything downstream wants the id: the brief looks the requirement's prose up
by id, and survival is scored by intersecting claims with the ids the oracle
reports passing.

Both directions live here because they are one fact. Split across the module
that mints the criterion and the modules that read it back, they drifted, and
nothing failed: the brief rendered "R01 is satisfied: no such requirement" for
every requirement a leaf was answerable for, so every executor worked from its
unit title alone; and the survival intersection compared criterion text against
bare ids, so the surviving count was zero in every cell of every run.

The criterion carries the requirement's TITLE as well as its id, because the
criterion is what a planner at depth is shown as the thing it must still cover
and the specification prose does not travel down with it: a child task's
description is the prose the level above wrote about that unit. An id alone is
not something a planner can allocate work against.
"""

import re
from collections.abc import Iterable, Sequence
from typing import NewType

from evals.errors import RecursionDepthClaimUnresolvableError

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
_CRITERION_TEMPLATE = "{identifier}: {title}"

#: A requirement id anywhere in a criterion, so a planner that rewords the
#: text around the id still resolves. The id itself is never reworded: it is
#: the token the specification and the oracle both key on.
_IDENTIFIER = re.compile(r"\bR\d+\b")


def criterion_for(identifier: RequirementId, title: str) -> str:
    """Render the acceptance criterion carrying *identifier*.

    Args:
        identifier: The requirement the criterion is for.
        title: Its one-line title, so a planner reading the criterion at depth
            can allocate work against it without the specification in front of
            it.

    Returns:
        The criterion text filed on the root objective.
    """
    return _CRITERION_TEMPLATE.format(identifier=identifier, title=title)


def requirement_ids_of(
    claims: Iterable[str], *, known: Sequence[RequirementId], unit: str
) -> tuple[RequirementId, ...]:
    """Resolve *claims* to the spec requirement ids they name.

    A claim resolves on the id token it contains, checked against *known*, so
    a planner that rewords the criterion still scores. One that names no
    requirement at all RAISES: passed through it reaches the brief as "no such
    requirement" and the survival intersection as a string that matches
    nothing, both of which read as an ordinary zero, and dropped with a warning
    it is 143 silent deflations of the ratio the sweep exists to measure.

    Raising here is the backstop. The product refuses such a claim at the
    boundary the planner writes it, where the session can still correct it, so
    reaching this means that boundary regressed and no measurement taken from
    the tree would mean anything.

    Args:
        claims: What the planner declared for one unit.
        known: Every requirement id the specification defines.
        unit: The unit the claims belong to, for the message.

    Returns:
        The resolved ids, deduplicated, in the order first seen.

    Raises:
        RecursionDepthClaimUnresolvableError: A claim names no requirement the
            specification defines.
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
    for claim in claims:
        found = [
            RequirementId(one)
            for one in _IDENTIFIER.findall(claim)
            if one in vocabulary
        ]
        if not found:
            msg = (
                f"unit {unit!r} claims {claim!r}, which names none of the "
                f"{len(vocabulary)} requirements this specification defines, "
                f"so nothing it delivers can be attributed"
            )
            raise RecursionDepthClaimUnresolvableError(msg)
        for identifier in found:
            if identifier not in seen:
                seen.add(identifier)
                resolved.append(identifier)
    return tuple(resolved)


__all__ = [
    "RequirementId",
    "criterion_for",
    "requirement_ids_of",
]
