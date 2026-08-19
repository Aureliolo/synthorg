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


def plan_risk_level(titles: Sequence[str]) -> ApprovalRiskLevel:
    """Scale plan-approval risk with the size of the plan.

    Args:
        titles: One title per item the plan proposes.

    Returns:
        ``LOW`` for a small plan, ``MEDIUM`` for a mid-sized one, ``HIGH``
        for a large plan (more items commit more work in one approval).
    """
    if len(titles) <= _LOW_RISK_MAX_SUBTASKS:
        return ApprovalRiskLevel.LOW
    if len(titles) <= _MEDIUM_RISK_MAX_SUBTASKS:
        return ApprovalRiskLevel.MEDIUM
    return ApprovalRiskLevel.HIGH


def plan_detail(titles: Sequence[str]) -> str:
    """Human-readable one-line summary of a plan.

    Args:
        titles: One title per item the plan proposes.

    Returns:
        A ``"<n> subtask(s): title, title, ..."`` summary.
    """
    preview = ", ".join(titles[:_PREVIEW_SUBTASKS])
    suffix = ", ..." if len(titles) > _PREVIEW_SUBTASKS else ""
    head = f"{len(titles)} subtask(s)"
    return f"{head}: {preview}{suffix}" if preview else f"{head} awaiting approval"


__all__ = ["plan_detail", "plan_risk_level"]
