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
bare ids, so ``surviving_claims`` was zero in every cell of every run, which is
the one number the sweep exists to produce.
"""

import re
from collections.abc import Iterable, Sequence

from synthorg.observability import get_logger

logger = get_logger(__name__)

#: The criterion a requirement id is filed as on the root objective.
_CRITERION_TEMPLATE = "{identifier} is satisfied"

#: A requirement id anywhere in a criterion, so a planner that rewords the
#: text around the id still resolves. The id itself is never reworded: it is
#: the token the specification and the oracle both key on.
_IDENTIFIER = re.compile(r"\bR\d+\b")


def criterion_for(identifier: str) -> str:
    """Render the acceptance criterion carrying *identifier*.

    Returns:
        The criterion text filed on the root objective.
    """
    return _CRITERION_TEMPLATE.format(identifier=identifier)


def requirement_ids_of(
    claims: Iterable[str], *, known: Sequence[str], unit: str
) -> tuple[str, ...]:
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
        The resolved ids, deduplicated, in the order first seen.
    """
    vocabulary = frozenset(known)
    resolved: list[str] = []
    for claim in claims:
        found = [one for one in _IDENTIFIER.findall(claim) if one in vocabulary]
        if not found:
            logger.warning(
                "evals.recursion_depth.claim_unresolved", unit=unit, claim=claim
            )
            continue
        resolved.extend(one for one in found if one not in resolved)
    return tuple(resolved)


__all__ = ["criterion_for", "requirement_ids_of"]
