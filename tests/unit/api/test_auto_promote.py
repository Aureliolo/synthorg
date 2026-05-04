"""Tests for the _maybe_promote_first_owner startup helper."""

from datetime import UTC, datetime

import pytest

from synthorg.api.approval_store import ApprovalStore
from synthorg.api.lifecycle_helpers import _maybe_promote_first_owner
from synthorg.api.state import AppState
from synthorg.config.schema import RootConfig
from synthorg.core.auth.models import OrgRole, User
from synthorg.core.auth.roles import HumanRole
from tests.unit.api.fakes import FakePersistenceBackend


async def _make_app_state(
    *,
    persistence: FakePersistenceBackend | None = None,
) -> AppState:
    """Build an AppState with optional fake persistence."""
    return AppState(
        config=RootConfig(company_name="test-company"),
        approval_store=ApprovalStore(),
        persistence=persistence,
    )


def _make_user(
    user_id: str,
    *,
    org_roles: tuple[OrgRole, ...] = (),
) -> User:
    """Build a test user with given org_roles."""
    now = datetime.now(UTC)
    return User(
        id=user_id,
        username=f"user-{user_id}",
        password_hash="$argon2id$fake-hash",
        role=HumanRole.MANAGER,
        must_change_password=False,
        org_roles=org_roles,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.unit
class TestMaybePromoteFirstOwner:
    async def test_no_persistence_returns_without_action(self) -> None:
        app_state = await _make_app_state(persistence=None)
        # Should not raise -- graceful skip
        await _maybe_promote_first_owner(app_state)

    async def test_no_users_returns_without_action(self) -> None:
        backend = FakePersistenceBackend()
        await backend.connect()
        app_state = await _make_app_state(persistence=backend)
        await _maybe_promote_first_owner(app_state)
        # No users exist, nothing to promote
        users = await backend.users.list_users()
        assert len(users) == 0

    async def test_already_has_owner_is_noop(self) -> None:
        backend = FakePersistenceBackend()
        await backend.connect()
        owner = _make_user("owner-1", org_roles=(OrgRole.OWNER,))
        other = _make_user("other-1")
        await backend.users.save(owner)
        await backend.users.save(other)

        app_state = await _make_app_state(persistence=backend)
        await _maybe_promote_first_owner(app_state)

        # Owner should remain unchanged, other should NOT be promoted
        refreshed_other = await backend.users.get("other-1")
        assert refreshed_other is not None
        assert OrgRole.OWNER not in refreshed_other.org_roles

    async def test_no_owner_promotes_first_user(self) -> None:
        backend = FakePersistenceBackend()
        await backend.connect()
        first = _make_user("first-1")
        second = _make_user("second-1")
        await backend.users.save(first)
        await backend.users.save(second)

        app_state = await _make_app_state(persistence=backend)
        await _maybe_promote_first_owner(app_state)

        # First user should now have OWNER
        promoted = await backend.users.get("first-1")
        assert promoted is not None
        assert OrgRole.OWNER in promoted.org_roles

        # Second user should NOT have OWNER
        not_promoted = await backend.users.get("second-1")
        assert not_promoted is not None
        assert OrgRole.OWNER not in not_promoted.org_roles

    async def test_persistence_error_graceful_skip(self) -> None:
        """Persistence that raises on list_users should be skipped."""
        backend = FakePersistenceBackend()
        await backend.connect()

        # Monkey-patch list_users to raise
        async def _broken_list_users() -> tuple[User, ...]:
            msg = "DB connection lost"
            raise RuntimeError(msg)

        backend._users.list_users = _broken_list_users

        app_state = await _make_app_state(persistence=backend)
        # Should not raise
        await _maybe_promote_first_owner(app_state)
