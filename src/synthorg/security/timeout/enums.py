"""Timeout policy action enumeration."""

from enum import StrEnum


class TimeoutActionType(StrEnum):
    """Action to take when an approval item times out.

    See ``docs/design/security.md``.
    """

    WAIT = "wait"
    APPROVE = "approve"
    DENY = "deny"
    ESCALATE = "escalate"
