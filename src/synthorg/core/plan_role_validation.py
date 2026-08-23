# module-kind: code
"""Whether a plan item's declared owner may hold it, decided in one place.

Two questions meet in this rule and they are not the same one. Whether a role
is STAFFED depends entirely on the roster. Whether a role may own work at all
does not, and answering that one second is what let a judge through: the roster
derivation excludes gate roles, so an org whose active agents are all judges
derives an empty roster, and an empty roster is read as "no roster known" and
passes everything.

Its own module rather than a paragraph inside the plan invariants, because it
is the single owner of that answer and the boundaries that ask it (the planning
session, the operator's plan edit, the manual strategy) reach it from three
different layers.
"""

from synthorg.core.role_catalog import role_is_gate_role
from synthorg.core.types import NotBlankStr


def describe_unroutable_role(
    *,
    entity_id: str,
    required_role: str | None,
    available_roles: tuple[NotBlankStr, ...],
) -> str | None:
    """Describe why an owning role cannot be routed, or ``None`` when it can.

    A plan item's owner is the role a dispatch looks up, so an invented one
    (the near-miss "Backend Engineer" for an org staffing "Backend Developer")
    produces an item with nobody behind it, discovered at dispatch if at all.

    Returns a message rather than raising, because the same judgement is made
    at two boundaries that report differently: decomposition turns it into a
    correctable ``DecompositionError`` the planning session can resubmit
    against, and the operator edit path into a validation failure. One
    wording, two reports.

    A gate role is refused before the roster is consulted at all, because the
    answer does not depend on staffing: it JUDGES work rather than performing
    it, so it cannot own a plan item however many agents hold it. Asking the
    roster first would make the refusal conditional on a set the roster
    derivation has already excluded the role from, and an org staffing nothing
    BUT judges would derive an empty roster and take the pass below, waving
    through the one owner this exists to refuse. It also covers the paths no
    roster derivation reaches, an operator hand-editing an owner among them.

    An empty roster means "no roster known" and passes: an org with no agents
    has nothing to check against, and failing there would block a greenlight
    for a reason unrelated to the plan.

    Args:
        entity_id: Identifier of the plan item / subtask, for the message.
        required_role: The declared owner, or ``None`` when unowned.
        available_roles: The roles the org actually staffs.

    Returns:
        A message naming the offending role and the valid set, or ``None``.
    """
    if required_role is None:
        return None
    if role_is_gate_role(required_role):
        return (
            f"{entity_id!r} names required_role {required_role!r}, which judges "
            f"finished work rather than performing it, so it cannot own a plan "
            f"item. Name the role that will do the work; the review gate "
            f"selects its own judge."
        )
    if not available_roles:
        return None
    if required_role in available_roles:
        return None
    valid = ", ".join(sorted(available_roles))
    return (
        f"{entity_id!r} names required_role {required_role!r}, which no agent "
        f"holds, so the item cannot be routed. Available roles: {valid}"
    )


__all__ = ["describe_unroutable_role"]
