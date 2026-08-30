# module-kind: declarative
"""What makes an approval THE "extend this workstream" decision.

Its own action type and its own metadata keys, parallel to
:mod:`initiative_stall` rather than sharing it: an extension ask and a stall
resolve differently (one grafts more work onto a live, still-executing
plan; the other replans or ends the whole initiative) and must not share
one idempotency key -- an operator answering "keep going" on a stall must
never be read as having answered an extension ask for some other leaf, or the
reverse.
"""

from typing import Final

#: A workstream's extension needs another one and the deterministic autonomy
#: gate requires a person to say so.
INITIATIVE_EXTENSION_ACTION_TYPE: Final[str] = "initiative:extension_ask"

#: Who raises this decision. See ``initiative_stall.ESCALATION_ACTOR`` for
#: why provenance is checked rather than assumed on resume.
EXTENSION_ESCALATION_ACTOR: Final[str] = "initiative-extension-rollup"

#: ``ApprovalItem.metadata`` key naming the plan the decision is about.
PLAN_ID_METADATA_KEY: Final[str] = "plan_id"

#: ``ApprovalItem.metadata`` key naming the workstream (the plan's top-level
#: item) the leaf sits under.
WORKSTREAM_ID_METADATA_KEY: Final[str] = "workstream_id"

#: ``ApprovalItem.metadata`` key naming the oversized-and-completed leaf the
#: ask is about. The idempotency key: a decision already open for this
#: exact (plan, leaf) pair means the rollup must not raise a second one, and
#: a resolved rejection for it means the rollup must not ask again.
LEAF_ID_METADATA_KEY: Final[str] = "leaf_id"


def is_initiative_extension_ask(action_type: str) -> bool:
    """Return whether *action_type* marks the extend-workstream decision.

    Returns:
        ``True`` only for that decision, never for anything else parked
        against the same plan.
    """
    return action_type == INITIATIVE_EXTENSION_ACTION_TYPE


__all__ = [
    "EXTENSION_ESCALATION_ACTOR",
    "INITIATIVE_EXTENSION_ACTION_TYPE",
    "LEAF_ID_METADATA_KEY",
    "PLAN_ID_METADATA_KEY",
    "WORKSTREAM_ID_METADATA_KEY",
    "is_initiative_extension_ask",
]
