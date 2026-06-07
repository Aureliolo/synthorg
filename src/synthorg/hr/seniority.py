"""Seniority levels and seniority comparison for organisation agents."""

from enum import StrEnum


class SeniorityLevel(StrEnum):
    """Seniority levels for agents within the organisation.

    Each level corresponds to an authority scope, typical model tier, and
    cost tier defined in ``synthorg.core.role_catalog.SENIORITY_INFO``.
    """

    # Agents page lists "Intern/Junior" -- collapsed to JUNIOR.
    JUNIOR = "junior"
    MID = "mid"
    SENIOR = "senior"
    LEAD = "lead"
    PRINCIPAL = "principal"
    DIRECTOR = "director"
    VP = "vp"
    C_SUITE = "c_suite"


# Authoritative authority ranking, junior to senior.  Spelled out
# explicitly (rather than ``tuple(SeniorityLevel)``) so that adding a
# new SeniorityLevel member forces a conscious placement here; the guard
# below then fails loudly if the two fall out of sync.
_SENIORITY_ORDER: tuple[SeniorityLevel, ...] = (
    SeniorityLevel.JUNIOR,
    SeniorityLevel.MID,
    SeniorityLevel.SENIOR,
    SeniorityLevel.LEAD,
    SeniorityLevel.PRINCIPAL,
    SeniorityLevel.DIRECTOR,
    SeniorityLevel.VP,
    SeniorityLevel.C_SUITE,
)

# Validate that _SENIORITY_ORDER contains every SeniorityLevel member
# exactly once.  This guards against silent breakage when the enum is
# extended without updating the ordering tuple above.
_all_members = set(SeniorityLevel)
_order_set = set(_SENIORITY_ORDER)
if _order_set != _all_members:
    _missing = _all_members - _order_set
    _extra = _order_set - _all_members
    _msg = (
        f"_SENIORITY_ORDER is out of sync with SeniorityLevel: "
        f"missing={_missing}, extra={_extra}"
    )
    raise RuntimeError(_msg)
if len(_SENIORITY_ORDER) != len(_order_set):
    _msg = "_SENIORITY_ORDER contains duplicate entries"
    raise RuntimeError(_msg)
del _all_members, _order_set

# Precomputed rank lookup for O(1) seniority comparison.
_SENIORITY_RANK: dict[SeniorityLevel, int] = {
    level: idx for idx, level in enumerate(_SENIORITY_ORDER)
}


def compare_seniority(a: SeniorityLevel, b: SeniorityLevel) -> int:
    """Compare two seniority levels.

    Returns negative if *a* is junior to *b*, zero if equal,
    positive if *a* is senior to *b*.

    Args:
        a: First seniority level.
        b: Second seniority level.

    Returns:
        Integer indicating relative seniority.
    """
    return _SENIORITY_RANK[a] - _SENIORITY_RANK[b]
