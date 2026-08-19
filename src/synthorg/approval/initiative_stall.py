# module-kind: declarative
"""What makes an approval THE "this initiative is out of road" decision.

``ApprovalSource.REVIEW_GATE`` says where an approval came from, not what it
asks, and it is the documented catch-all for everything that is neither a
parked context nor a conversational flow. So the discriminator is the action
type, exactly as it is for the plan gate and the hiring request, and it lives
here rather than in the module that mints it because the router that must
recognise it does not own that module.
"""

from typing import Final

#: An initiative can no longer advance on its own and a person must decide
#: whether it continues.
INITIATIVE_STALL_ACTION_TYPE: Final[str] = "initiative:stalled"

#: ``ApprovalItem.metadata`` key naming the plan the decision is about. Here
#: rather than beside the code that writes it, because the engine writes it and
#: the API reads it back: putting it in either would make the other import a
#: package it has no other reason to touch.
PLAN_ID_METADATA_KEY: Final[str] = "plan_id"

#: ``ApprovalItem.metadata`` key naming the project the plan belongs to.
PROJECT_METADATA_KEY: Final[str] = "project"

#: ``ApprovalItem.metadata`` key naming which refusal raised the decision, so a
#: surface can say whether the budget ran out or the switch is off.
DISPOSITION_METADATA_KEY: Final[str] = "disposition"


def is_initiative_stall(action_type: str) -> bool:
    """Return whether *action_type* marks the stalled-initiative decision.

    Returns:
        ``True`` only for that decision, never for anything else parked
        against the same plan.
    """
    return action_type == INITIATIVE_STALL_ACTION_TYPE


__all__ = [
    "DISPOSITION_METADATA_KEY",
    "INITIATIVE_STALL_ACTION_TYPE",
    "PLAN_ID_METADATA_KEY",
    "PROJECT_METADATA_KEY",
    "is_initiative_stall",
]
