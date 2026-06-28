"""Shared old/new presence invariants for change-record models.

Both workflow-definition diffs (``engine.workflow.diff``) and agent-identity
diffs (``engine.identity.diff``) record a ``change_type`` alongside an
``old_value`` / ``new_value`` pair. The presence rule is identical: an added
change carries only the new value, a removed change carries only the old
value, and every other change type carries both. This module is the single
source of that rule so the two diff families cannot drift.
"""


def validate_change_invariants(
    change_type: str,
    old_value: object | None,
    new_value: object | None,
) -> None:
    """Enforce the old/new presence rule for a single change record.

    ``"added"`` requires ``old_value is None`` and ``new_value`` set;
    ``"removed"`` requires ``new_value is None`` and ``old_value`` set; any
    other ``change_type`` (a modification) requires both values present.

    Args:
        change_type: The kind of change being recorded.
        old_value: The value before the change (``None`` for additions).
        new_value: The value after the change (``None`` for removals).

    Raises:
        ValueError: When the presence of ``old_value`` / ``new_value`` does
            not match the rule for ``change_type``.
    """
    if change_type == "added":
        if old_value is not None:
            msg = "change_type 'added' requires old_value=None"
            raise ValueError(msg)
        if new_value is None:
            msg = "change_type 'added' requires new_value to be set"
            raise ValueError(msg)
    elif change_type == "removed":
        if new_value is not None:
            msg = "change_type 'removed' requires new_value=None"
            raise ValueError(msg)
        if old_value is None:
            msg = "change_type 'removed' requires old_value to be set"
            raise ValueError(msg)
    elif old_value is None or new_value is None:
        msg = f"change_type {change_type!r} requires both old_value and new_value"
        raise ValueError(msg)
