"""Workspace merge domain enumerations."""

from enum import StrEnum


class MergeOrder(StrEnum):
    """Order in which workspace branches are merged back.

    Determines the sequence of merge operations when multiple
    agent workspaces are being merged into the base branch.
    """

    COMPLETION = "completion"
    PRIORITY = "priority"
    MANUAL = "manual"


class ConflictEscalation(StrEnum):
    """Strategy for handling merge conflicts during workspace merges.

    Controls whether merging stops for human review or continues
    with an automated review agent flagging conflicts.
    """

    HUMAN = "human"
    REVIEW_AGENT = "review_agent"


class ConflictType(StrEnum):
    """Type of merge conflict detected during workspace merges."""

    TEXTUAL = "textual"
    SEMANTIC = "semantic"
