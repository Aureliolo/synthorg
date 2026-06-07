"""Seniority levels and seniority comparison for organisation agents."""

from enum import StrEnum


class SeniorityLevel(StrEnum):
    """Seniority levels for agents within the organization.

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


_SENIORITY_ORDER: tuple[SeniorityLevel, ...] = tuple(SeniorityLevel)

# Validate that _SENIORITY_ORDER contains every SeniorityLevel member
# exactly once and preserves enum declaration order.  This guards
# against silent breakage if the enum is reordered or extended without
# updating the ordering tuple.
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
