"""Shared validation utilities for domain value formats."""

_ACTION_TYPE_PARTS: int = 2


def is_valid_action_type(action_type: str) -> bool:
    """Check whether ``action_type`` follows ``category:action`` format.

    Args:
        action_type: The action type string to validate.

    Returns:
        ``True`` if the string has exactly one colon separating
        two non-blank segments, ``False`` otherwise.
    """
    parts = action_type.split(":")
    if len(parts) != _ACTION_TYPE_PARTS:
        return False
    return bool(parts[0].strip() and parts[1].strip())
