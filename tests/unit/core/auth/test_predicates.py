"""Tests for the shared owner-or-CEO authorisation predicate."""

import pytest

from synthorg.core.auth.models import AuthenticatedUser, AuthMethod
from synthorg.core.auth.predicates import is_owner_or_ceo
from synthorg.core.auth.roles import HumanRole


def _user(
    *, user_id: str = "u-owner", role: HumanRole = HumanRole.MANAGER
) -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id=user_id,
        username="alice",
        role=role,
        auth_method=AuthMethod.JWT,
    )


@pytest.mark.unit
class TestIsOwnerOrCeo:
    def test_owner_matches(self) -> None:
        assert is_owner_or_ceo(_user(user_id="u-1"), "u-1") is True

    def test_non_owner_non_ceo_denied(self) -> None:
        assert is_owner_or_ceo(_user(user_id="u-1"), "u-2") is False

    def test_ceo_overrides_non_ownership(self) -> None:
        ceo = _user(user_id="u-1", role=HumanRole.CEO)
        assert is_owner_or_ceo(ceo, "u-2") is True

    def test_none_owner_denied_for_non_ceo(self) -> None:
        assert is_owner_or_ceo(_user(user_id="u-1"), None) is False

    def test_none_owner_allowed_for_ceo(self) -> None:
        ceo = _user(user_id="u-1", role=HumanRole.CEO)
        assert is_owner_or_ceo(ceo, None) is True
