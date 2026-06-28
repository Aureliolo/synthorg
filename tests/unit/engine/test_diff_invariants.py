"""Direct tests for the shared change-record presence invariant.

``validate_change_invariants`` is the single source of the old/new presence
rule for both the workflow-definition and agent-identity diff families, so it
is pinned here independently of either consuming model.
"""

import pytest

from synthorg.engine._diff_invariants import validate_change_invariants


@pytest.mark.unit
@pytest.mark.parametrize(
    ("change_type", "old_value", "new_value"),
    [
        ("added", None, "new"),
        ("removed", "old", None),
        ("modified", "old", "new"),
        ("moved", "old", "new"),
    ],
)
def test_valid_combinations_pass(
    change_type: str,
    old_value: object | None,
    new_value: object | None,
) -> None:
    validate_change_invariants(change_type, old_value, new_value)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("change_type", "old_value", "new_value", "error_match"),
    [
        ("added", "prev", "new", r"change_type 'added' requires old_value=None"),
        ("added", None, None, r"change_type 'added' requires new_value to be set"),
        ("removed", "old", "here", r"change_type 'removed' requires new_value=None"),
        ("removed", None, None, r"change_type 'removed' requires old_value to be set"),
        ("modified", None, "new", r"requires both old_value and new_value"),
        ("modified", "old", None, r"requires both old_value and new_value"),
        ("moved", None, "new", r"change_type 'moved' requires both"),
        ("moved", "old", None, r"change_type 'moved' requires both"),
    ],
)
def test_invalid_combinations_raise(
    change_type: str,
    old_value: object | None,
    new_value: object | None,
    error_match: str,
) -> None:
    with pytest.raises(ValueError, match=error_match):
        validate_change_invariants(change_type, old_value, new_value)
