# module-kind: code
"""Seniority-level adjacency helpers for the promotion service.

Pure level-arithmetic over :class:`SeniorityLevel`, factored out of
``service.py`` so the orchestrator stays within its module-size budget.
"""

from synthorg.hr.seniority import SeniorityLevel


def next_level(level: SeniorityLevel) -> SeniorityLevel | None:
    """Get the next higher seniority level, or None at top.

    Returns:
        The resulting ``SeniorityLevel``, or ``None`` when unavailable.
    """
    members = list(SeniorityLevel)
    idx = members.index(level)
    if idx + 1 >= len(members):
        return None
    return members[idx + 1]


def prev_level(level: SeniorityLevel) -> SeniorityLevel | None:
    """Get the next lower seniority level, or None at bottom.

    Returns:
        The resulting ``SeniorityLevel``, or ``None`` when unavailable.
    """
    members = list(SeniorityLevel)
    idx = members.index(level)
    if idx <= 0:
        return None
    return members[idx - 1]
