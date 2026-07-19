# module-kind: code
"""Role-to-agent resolution shared across the conversational surfaces.

Resolving a role name to the active agent that holds it (most senior first,
name as a deterministic tiebreak) is needed by concern routing, the unified
turn orchestrator's explicit ``@role`` addressing and group convening, and
the plan-item reply responder. Holding the resolution in one leaf module
keeps that rule in a single place rather than copied per caller.
"""

import functools

from synthorg.core.agent import AgentIdentity
from synthorg.core.authority import compare_authority
from synthorg.core.normalization import compare_ci
from synthorg.core.types import NotBlankStr


def _by_seniority_then_name(a: AgentIdentity, b: AgentIdentity) -> int:
    """Order two role-holders most-senior-first, then name ascending.

    Returns:
        Negative when *a* sorts before *b*, positive when after, zero
        when identical on both keys.
    """
    by_authority = compare_authority(b.role, a.role)
    if by_authority != 0:
        return by_authority
    return (a.name > b.name) - (a.name < b.name)


def resolve_agent_for_role(
    active: tuple[AgentIdentity, ...], role: NotBlankStr
) -> AgentIdentity | None:
    """Find the active agent holding *role* (case-insensitive).

    When several active agents share a role, the most senior is chosen
    (the natural primary for the role), with the alphabetically-first by
    name as a deterministic tiebreak across equal-seniority holders.

    Returns:
        The matching identity, or ``None`` when no active agent holds it.
    """
    matches = [a for a in active if compare_ci(a.role, role)]
    if not matches:
        return None
    return min(matches, key=functools.cmp_to_key(_by_seniority_then_name))


__all__ = ["resolve_agent_for_role"]
