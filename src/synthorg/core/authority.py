# module-kind: code
"""Authority derived from the role reporting graph.

Authority is a structural property of an agent's position in the
organisation, not a scalar rank: a role's standing follows the
``Role.reports_to`` chain up to the CEO (a root with ``reports_to
is None``). These helpers answer the question the org actually asks:
"who is the most senior agent here?" (the shallowest reporting depth),
via a reporting-depth lookup and a pairwise seniority comparison.

The graph is resolved over the built-in role catalog. A role unknown
to the catalog (a bespoke custom role) resolves to the least-senior
depth and outranks no one, which fails safe: an unrecognised position
never wins an authority contest by default.
"""

from typing import Final

from synthorg.core.normalization import normalize_identifier
from synthorg.core.role_catalog import get_builtin_role

# Depth assigned to a role the catalog cannot resolve (or whose chain
# never reaches a root): larger than any real chain, so it always ranks
# as the most junior.
_UNKNOWN_DEPTH: Final[int] = 1_000


def _reports_to(role_name: str) -> str | None:
    """Return the immediate superior role name, or ``None`` at a root.

    Returns:
        The ``reports_to`` role name of the built-in role matching
        *role_name*, or ``None`` when the role is a root or is not a
        built-in role.
    """
    role = get_builtin_role(role_name)
    if role is None:
        return None
    return role.reports_to


def reporting_chain(role_name: str) -> tuple[str, ...]:
    """Return the chain of superiors above *role_name*, nearest first.

    Walks ``reports_to`` from the immediate manager up to the root.
    Names are normalised for stable comparison. A cycle (which a valid
    catalog never contains) terminates the walk defensively.

    Returns:
        Normalised superior role names ordered immediate-manager-first,
        empty when the role is a root or is not a built-in role.
    """
    chain: list[str] = []
    seen: set[str] = {normalize_identifier(role_name)}
    current = _reports_to(role_name)
    while current is not None:
        key = normalize_identifier(current)
        if key in seen:
            break
        chain.append(key)
        seen.add(key)
        current = _reports_to(current)
    return tuple(chain)


def role_depth(role_name: str) -> int:
    """Return the number of reporting hops from *role_name* to a root.

    A root (the CEO) has depth ``0``; each ``reports_to`` hop adds one.
    A role the catalog cannot resolve returns :data:`_UNKNOWN_DEPTH` so
    it ranks as the most junior.

    Returns:
        The reporting depth (smaller is more senior).
    """
    if get_builtin_role(role_name) is None:
        return _UNKNOWN_DEPTH
    return len(reporting_chain(role_name))


def compare_authority(a_role: str, b_role: str) -> int:
    """Compare two roles by reporting depth (more senior is greater).

    Sign contract: negative when *a_role* is junior to *b_role*, zero
    when they sit at equal depth, positive when *a_role* is more senior.

    Returns:
        ``role_depth(b_role) - role_depth(a_role)``.
    """
    return role_depth(b_role) - role_depth(a_role)
