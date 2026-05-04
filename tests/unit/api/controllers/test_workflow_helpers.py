"""Tests for ``synthorg.api.controllers._workflow_helpers``.

Covers ``get_auth_user_id`` and ``request_audit_actor`` against
both authenticated and anonymous request scopes.
"""

from typing import Any
from unittest.mock import MagicMock

import pytest

from synthorg.api.controllers._workflow_helpers import (
    get_auth_user_id,
    request_audit_actor,
)
from synthorg.core.auth.models import AuthenticatedUser, AuthMethod
from synthorg.core.auth.roles import HumanRole

pytestmark = pytest.mark.unit


def _make_request(user: Any) -> Any:
    """Build a minimal stub that satisfies the helpers' contract."""
    request = MagicMock()
    request.scope = {"user": user} if user is not None else {}
    request.url.path = "/api/v1/providers/test/models"
    return request


@pytest.fixture
def auth_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id="user-42",
        username="alice@example.com",
        role=HumanRole.CEO,
        auth_method=AuthMethod.JWT,
    )


class TestGetAuthUserId:
    def test_returns_user_id_when_authenticated(
        self,
        auth_user: AuthenticatedUser,
    ) -> None:
        request = _make_request(auth_user)
        assert get_auth_user_id(request) == "user-42"

    def test_returns_api_sentinel_when_anonymous(self) -> None:
        request = _make_request(None)
        assert get_auth_user_id(request) == "api"

    def test_returns_api_when_user_is_not_authenticated_user(self) -> None:
        # Some other object in scope (e.g. a raw dict) -> sentinel.
        request = _make_request({"id": "x"})
        assert get_auth_user_id(request) == "api"


class TestRequestAuditActor:
    def test_authenticated_actor_uses_user_id_and_username(
        self,
        auth_user: AuthenticatedUser,
    ) -> None:
        request = _make_request(auth_user)
        actor = request_audit_actor(request)
        assert actor.id == "user-42"
        assert actor.label == "alice@example.com"

    def test_anonymous_request_falls_back_to_api_sentinel(self) -> None:
        request = _make_request(None)
        actor = request_audit_actor(request)
        assert actor.id == "api"
        assert actor.label == "api"

    def test_non_authenticated_user_in_scope_falls_back(self) -> None:
        # Mirrors the get_auth_user_id contract: anything other than
        # an ``AuthenticatedUser`` instance is treated as anonymous.
        request = _make_request({"id": "spoof", "username": "spoof"})
        actor = request_audit_actor(request)
        assert actor.id == "api"
        assert actor.label == "api"
