"""Tests for the user constraint-token -> typed-conflict mapping."""

import pytest

from synthorg.api.auth.user_constraints import (
    DuplicateUsernameError,
    LastCeoConstraintError,
    LastOwnerConstraintError,
    SingleCeoConstraintError,
    raise_for_user_constraint,
)
from synthorg.core.constraint_tokens import (
    IDX_SINGLE_CEO,
    LAST_CEO_TRIGGER,
    LAST_OWNER_TRIGGER,
    USERS_USERNAME_UNIQUE,
)
from synthorg.core.domain_errors import ConflictError
from synthorg.core.persistence_errors import ConstraintViolationError

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        (USERS_USERNAME_UNIQUE, DuplicateUsernameError),
        (IDX_SINGLE_CEO, SingleCeoConstraintError),
        (LAST_CEO_TRIGGER, LastCeoConstraintError),
        (LAST_OWNER_TRIGGER, LastOwnerConstraintError),
    ],
)
def test_known_token_maps_to_typed_conflict(
    token: str,
    expected: type[ConflictError],
) -> None:
    exc = ConstraintViolationError("violated", constraint=token)
    with pytest.raises(expected) as caught:
        raise_for_user_constraint(exc)
    # All four map to a 409 conflict via the shared base.
    assert caught.value.status_code == 409
    assert isinstance(caught.value, ConflictError)


def test_unknown_token_reraises_original() -> None:
    exc = ConstraintViolationError("violated", constraint="SOME_OTHER_CONSTRAINT")
    with pytest.raises(ConstraintViolationError) as caught:
        raise_for_user_constraint(exc)
    assert caught.value is exc


def test_ceo_conflicts_mention_ceo_in_message() -> None:
    # The atomic controller test asserts ``"CEO" in error``; guard the
    # message contract at the source.
    assert "CEO" in str(SingleCeoConstraintError())
    assert "CEO" in str(LastCeoConstraintError())
