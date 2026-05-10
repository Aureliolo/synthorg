"""Tests for ``synthorg.api.controllers._workflow_helpers``.

Covers ``audit_actor_from_context`` (the request-bound audit actor
factory) and the ``BACKGROUND_AUDIT_ACTOR`` sentinel exposed for
background paths.
"""

import pytest

from synthorg.api.auth.context import (
    AuthContextMissingError,
    authenticated_user_scope,
)
from synthorg.api.controllers._workflow_helpers import (
    BACKGROUND_AUDIT_ACTOR,
    audit_actor_from_context,
)
from synthorg.core.auth.models import AuthenticatedUser, AuthMethod
from synthorg.core.auth.roles import HumanRole

pytestmark = pytest.mark.unit


@pytest.fixture
def auth_user() -> AuthenticatedUser:
    return AuthenticatedUser(
        user_id="user-42",
        username="alice@example.com",
        role=HumanRole.CEO,
        auth_method=AuthMethod.JWT,
    )


class TestAuditActorFromContext:
    async def test_returns_actor_from_bound_user(
        self,
        auth_user: AuthenticatedUser,
    ) -> None:
        async with authenticated_user_scope(auth_user):
            actor = audit_actor_from_context()
        assert actor.id == "user-42"
        assert actor.label == "alice@example.com"

    async def test_raises_when_no_user_bound(self) -> None:
        with pytest.raises(AuthContextMissingError):
            audit_actor_from_context()


class TestBackgroundAuditActor:
    def test_sentinel_uses_api_label(self) -> None:
        # Constant is the explicit opt-in for background paths that
        # legitimately have no authenticated user (scheduled jobs,
        # startup probes). Mirrors the previous "api" sentinel that
        # used to leak from request_audit_actor for unauthenticated
        # requests, but now requires an explicit reference.
        assert BACKGROUND_AUDIT_ACTOR.id == "api"
        assert BACKGROUND_AUDIT_ACTOR.label == "api"
