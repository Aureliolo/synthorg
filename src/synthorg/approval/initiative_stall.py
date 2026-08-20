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

#: Who raises this decision. Recorded as the requester so the audit trail never
#: attributes it to the operator who was the one being asked, and read back by
#: the resume flow as the item's provenance.
#:
#: Provenance is checked, not assumed. The action type says what a decision
#: ASKS; it does not say who asked. ``POST /approvals`` accepts an action type
#: and a metadata blob from any caller with write access, and the source this
#: decision uses is the default one, so an item minted there is otherwise
#: indistinguishable from one the organisation raised. Acting on such an item
#: would let a writer aim a plan failure, or a replan that lifts the operator's
#: budget, at any initiative they can name.
ESCALATION_ACTOR: Final[str] = "initiative-rollup"

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

#: ``ApprovalItem.metadata`` key naming the stall the decision was raised for.
#:
#: Load-bearing rather than informational. Re-confirming a stall takes one of
#: two forms depending on the reason, and only the reason says which: an
#: item-derived stall is re-derived over the live items, and a tail-stage
#: verdict is confirmed by the plan still sitting in the stage that produced
#: it. Deriving over items alone answers "not stalled" for every tail-stage
#: reason, because every item IS done in both of those cases, so the decision
#: a person just made would read as one about a plan that had recovered and
#: quietly do nothing to either answer.
REASON_METADATA_KEY: Final[str] = "stall_reason"


def is_initiative_stall(action_type: str) -> bool:
    """Return whether *action_type* marks the stalled-initiative decision.

    Returns:
        ``True`` only for that decision, never for anything else parked
        against the same plan.
    """
    return action_type == INITIATIVE_STALL_ACTION_TYPE


__all__ = [
    "DISPOSITION_METADATA_KEY",
    "ESCALATION_ACTOR",
    "INITIATIVE_STALL_ACTION_TYPE",
    "PLAN_ID_METADATA_KEY",
    "PROJECT_METADATA_KEY",
    "REASON_METADATA_KEY",
    "is_initiative_stall",
]
