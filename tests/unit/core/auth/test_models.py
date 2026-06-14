"""Tests for ``synthorg.core.auth.models`` invariants."""

import pytest
from pydantic import ValidationError

from synthorg.core.auth.models import AuthenticatedUser, AuthMethod
from synthorg.core.auth.roles import HumanRole

pytestmark = pytest.mark.unit


def test_api_key_method_requires_api_key_id() -> None:
    with pytest.raises(ValidationError, match="api_key_id is required"):
        AuthenticatedUser(
            user_id="u-1",
            username="alice",
            role=HumanRole.MANAGER,
            auth_method=AuthMethod.API_KEY,
        )


def test_non_api_key_method_rejects_api_key_id() -> None:
    with pytest.raises(ValidationError, match="api_key_id must be None"):
        AuthenticatedUser(
            user_id="u-1",
            username="alice",
            role=HumanRole.MANAGER,
            auth_method=AuthMethod.JWT,
            api_key_id="key-1",
        )


def test_api_key_method_with_api_key_id_is_valid() -> None:
    user = AuthenticatedUser(
        user_id="u-1",
        username="alice",
        role=HumanRole.MANAGER,
        auth_method=AuthMethod.API_KEY,
        api_key_id="key-1",
    )
    assert user.api_key_id == "key-1"


def test_jwt_method_without_api_key_id_is_valid() -> None:
    user = AuthenticatedUser(
        user_id="u-1",
        username="alice",
        role=HumanRole.MANAGER,
        auth_method=AuthMethod.JWT,
    )
    assert user.api_key_id is None
