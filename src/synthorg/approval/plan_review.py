# module-kind: declarative
"""What makes an approval THE plan-approval gate.

``ApprovalSource.PLAN_REVIEW`` says where an approval came from, not what it
asks. A parked plan and every open question parked beside it share that source,
so a router that owns "the plan-review source" owns the questions too: answering
one approved the plan and dispatched the work, while the gate's own approval sat
undecided in the operator's queue.

The action type is the discriminator, and it lives here rather than in the gate
that mints it because the router that must recognise exactly one approval does
not own the module that creates it.
"""

from typing import Final

#: A decomposed plan is parked for a human to approve before anything builds.
PLAN_APPROVAL_ACTION_TYPE: Final[str] = "plan:approve"


def is_plan_approval(action_type: str) -> bool:
    """Return whether *action_type* marks an approval as the plan gate itself.

    Returns:
        ``True`` only for the plan's own approval, never for a question parked
        alongside it.
    """
    return action_type == PLAN_APPROVAL_ACTION_TYPE


__all__ = [
    "PLAN_APPROVAL_ACTION_TYPE",
    "is_plan_approval",
]
