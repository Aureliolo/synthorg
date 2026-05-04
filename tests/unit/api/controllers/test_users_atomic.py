"""Tests for atomic user operations after _CEO_LOCK removal.

Verifies that the user controller correctly handles DB constraint
violations (unique index, triggers) without relying on an in-process
asyncio.Lock.  The DB is the sole enforcement mechanism; the fake
backend simulates the same constraints.
"""

import uuid
from typing import Any

import pytest
from litestar.testing import TestClient

from tests.unit.api.conftest import make_auth_headers
from tests.unit.api.fakes import FakePersistenceBackend

_BASE = "/api/v1/users"
_CEO_HEADERS = make_auth_headers("ceo")


def _create_payload(**overrides: Any) -> dict[str, Any]:
    defaults: dict[str, Any] = {
        "username": "new-user",
        "password": "secure-password-12chars",
        "role": "manager",
    }
    return {**defaults, **overrides}


# ── Module-level lock must not exist ──────────────────────────


@pytest.mark.unit
class TestNoModuleLevelLock:
    """Verify the asyncio.Lock was actually removed."""

    def test_ceo_lock_removed(self) -> None:
        from synthorg.api.controllers import users

        assert not hasattr(users, "_CEO_LOCK"), (
            "_CEO_LOCK still exists -- it should be removed"
        )

    def test_validate_ceo_create_removed(self) -> None:
        from synthorg.api.controllers import users

        assert not hasattr(users, "_validate_ceo_create"), (
            "_validate_ceo_create should be removed"
        )

    def test_validate_role_change_removed(self) -> None:
        from synthorg.api.controllers import users

        assert not hasattr(users, "_validate_role_change"), (
            "_validate_role_change should be removed"
        )


# ── CEO creation constraint handling ──────────────────────────


@pytest.mark.unit
class TestCEOCreationConstraint:
    """Controller maps QueryError from save() to ConflictError (409)."""

    def test_second_ceo_returns_409(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Creating a second CEO gets 409 from DB constraint."""
        resp = test_client.post(
            _BASE,
            json=_create_payload(username="ceo2", role="ceo"),
            headers=_CEO_HEADERS,
        )
        assert resp.status_code == 409

    def test_duplicate_username_returns_409(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Duplicate username gets 409 from UNIQUE constraint."""
        test_client.post(
            _BASE,
            json=_create_payload(username="unique-user", role="manager"),
            headers=_CEO_HEADERS,
        )
        resp = test_client.post(
            _BASE,
            json=_create_payload(username="unique-user", role="observer"),
            headers=_CEO_HEADERS,
        )
        assert resp.status_code == 409


# ── Role change constraint handling ───────────────────────────


@pytest.mark.unit
class TestRoleChangeConstraint:
    """Controller maps role-change constraint violations to 409."""

    def test_update_to_ceo_when_ceo_exists_returns_409(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Promoting to CEO when one exists gets 409."""
        resp = test_client.post(
            _BASE,
            json=_create_payload(username="promote-target", role="manager"),
            headers=_CEO_HEADERS,
        )
        assert resp.status_code == 201
        user_id = resp.json()["data"]["id"]

        resp = test_client.patch(
            f"{_BASE}/{user_id}",
            json={"role": "ceo"},
            headers=_CEO_HEADERS,
        )
        assert resp.status_code == 409

    def test_demote_last_ceo_returns_409(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Demoting the only CEO gets 409 from DB trigger.

        The fake backend enforces the last-CEO constraint.
        """
        ceo_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "test-ceo"))

        resp = test_client.patch(
            f"{_BASE}/{ceo_id}",
            json={"role": "manager"},
            headers=_CEO_HEADERS,
        )
        assert resp.status_code == 409
        assert "CEO" in resp.json().get("error", "")


# ── Owner revocation constraint handling ──────────────────────


@pytest.mark.unit
class TestOwnerRevocationConstraint:
    """Controller maps last-owner trigger violation to 409."""

    def test_revoke_last_owner_returns_409(
        self,
        test_client: TestClient[Any],
    ) -> None:
        """Revoking the last owner gets 409 from DB trigger.

        The fake backend enforces the last-owner constraint.
        The test_client fixture auto-promotes the first CEO user
        to owner on startup, so we just need to revoke that.
        """
        ceo_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "test-ceo"))

        resp = test_client.delete(
            f"{_BASE}/{ceo_id}/org-roles/owner",
            headers=_CEO_HEADERS,
        )
        assert resp.status_code == 409

    def test_delete_last_owner_returns_409(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        """DELETE user returns 409 when the user is the last owner.

        Sets up a manager as the sole owner (via direct fake
        mutation), then verifies DELETE maps the LAST_OWNER_TRIGGER
        constraint to a 409 response through the full ASGI stack.
        """
        from synthorg.core.auth.models import OrgRole

        ceo_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, "test-ceo"))
        manager_id = str(
            uuid.uuid5(uuid.NAMESPACE_DNS, "test-manager"),
        )

        # Transfer OWNER from CEO to manager so manager is the
        # sole owner and the delete trigger fires.
        users_by_id = fake_persistence._users._users
        ceo = users_by_id.get(ceo_id)
        manager = users_by_id.get(manager_id)
        assert ceo is not None, f"Missing seeded CEO user: {ceo_id}"
        assert manager is not None, f"Missing seeded manager: {manager_id}"
        users_by_id[ceo_id] = ceo.model_copy(
            update={"org_roles": ()},
        )
        users_by_id[manager_id] = manager.model_copy(
            update={"org_roles": (OrgRole.OWNER,)},
        )

        resp = test_client.delete(
            f"{_BASE}/{manager_id}",
            headers=_CEO_HEADERS,
        )
        assert resp.status_code == 409
        assert "owner" in resp.json().get("error", "").lower()
