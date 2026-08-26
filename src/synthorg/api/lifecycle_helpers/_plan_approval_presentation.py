# module-kind: code
"""How a plan presents itself in the approval queue.

Two pure readings of the same plan, kept away from the wiring that raises the
approval because they answer a different question: not "what happens to this
plan" but "what does the operator see before they decide".

Both take the item titles rather than a plan object, because two paths raise
this approval (a first-time plan, from its ``DecompositionResult``, and a
replan successor, from its persisted ``Plan``) and a second implementation is
how the two would come to describe the same decision differently.
"""

from collections.abc import Sequence
from typing import Final

from synthorg.approval.enums import ApprovalRiskLevel

_PREVIEW_SUBTASKS: Final[int] = 3

# Plan-approval risk scales with plan size: a larger plan commits more work and
# budget in one decision, so it warrants proportionally more scrutiny. (Risk
# level is otherwise a mostly-decorative label; scaling it with size at least
# makes it an honest signal here rather than a hardcoded constant.)
_LOW_RISK_MAX_SUBTASKS: Final[int] = 3
_MEDIUM_RISK_MAX_SUBTASKS: Final[int] = 8


def plan_risk_level(total_units: int) -> ApprovalRiskLevel:
    """Scale plan-approval risk with the size of the plan.

    Counted over the whole tree rather than its top level. What the approval
    commits is every unit under it, so a plan of two workstreams holding
    ninety units is not a small plan, and reading the top level alone would
    grade the largest plans the product can produce as its lowest risk.

    Args:
        total_units: How many units the whole plan holds, every level counted.

    Returns:
        ``LOW`` for a small plan, ``MEDIUM`` for a mid-sized one, ``HIGH``
        for a large plan (more items commit more work in one approval).
    """
    if total_units <= _LOW_RISK_MAX_SUBTASKS:
        return ApprovalRiskLevel.LOW
    if total_units <= _MEDIUM_RISK_MAX_SUBTASKS:
        return ApprovalRiskLevel.MEDIUM
    return ApprovalRiskLevel.HIGH


def plan_detail(titles: Sequence[str], *, total_units: int) -> str:
    """Human-readable one-line summary of a plan.

    A plan is a tree, so the two numbers are different questions and the
    summary answers both: *titles* names the coarse tracks the operator is
    approving, while *total_units* is how much work sits under them. Reading
    the preview's length as the size would report a hundred-unit plan as two.

    Args:
        titles: One title per workstream the plan proposes.
        total_units: How many units the whole plan holds, every level counted.

    Returns:
        A ``"<n> workstream(s), <m> unit(s): title, title, ..."`` summary.
    """
    preview = ", ".join(titles[:_PREVIEW_SUBTASKS])
    suffix = ", ..." if len(titles) > _PREVIEW_SUBTASKS else ""
    head = f"{len(titles)} workstream(s), {total_units} unit(s)"
    return f"{head}: {preview}{suffix}" if preview else f"{head} awaiting approval"


__all__ = ["plan_detail", "plan_risk_level"]
