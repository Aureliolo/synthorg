"""Tests for org-role grant and revoke endpoints on users."""

from datetime import UTC, datetime
from typing import Any

import pytest
from litestar.testing import TestClient

from synthorg.core.auth.models import OrgRole, User
from synthorg.core.auth.roles import HumanRole
from tests.unit.api.fakes import FakePersistenceBackend


def _seed_target_user(  # noqa: PLR0913
    fake_persistence: FakePersistenceBackend,
    *,
    user_id: str = "target-user-001",
    username: str = "target-manager",
    role: HumanRole = HumanRole.MANAGER,
    org_roles: tuple[OrgRole, ...] = (),
    scoped_departments: tuple[str, ...] = (),
) -> User:
    """Seed a target user directly into the fake persistence."""
    now = datetime.now(UTC)
    user = User(
        id=user_id,
        username=username,
        password_hash="$argon2id$fake-hash",
        role=role,
        must_change_password=False,
        org_roles=org_roles,
        scoped_departments=scoped_departments,
        created_at=now,
        updated_at=now,
    )
    fake_persistence._users._users[user.id] = user
    return user


@pytest.mark.unit
class TestGrantOrgRole:
    def test_grant_editor_happy_path(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        user = _seed_target_user(fake_persistence)
        resp = test_client.post(
            f"/api/v1/users/{user.id}/org-roles",
            json={"role": "editor"},
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert "editor" in data["org_roles"]

    def test_grant_owner_happy_path(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        user = _seed_target_user(fake_persistence)
        resp = test_client.post(
            f"/api/v1/users/{user.id}/org-roles",
            json={"role": "owner"},
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert "owner" in data["org_roles"]

    def test_grant_department_admin_without_scoped_departments_422(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        user = _seed_target_user(fake_persistence)
        resp = test_client.post(
            f"/api/v1/users/{user.id}/org-roles",
            json={"role": "department_admin", "scoped_departments": []},
        )
        assert resp.status_code == 422

    def test_grant_department_admin_with_departments(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        user = _seed_target_user(fake_persistence)
        resp = test_client.post(
            f"/api/v1/users/{user.id}/org-roles",
            json={
                "role": "department_admin",
                "scoped_departments": ["eng", "sales"],
            },
        )
        assert resp.status_code == 201
        data = resp.json()["data"]
        assert "department_admin" in data["org_roles"]
        assert "eng" in data["scoped_departments"]
        assert "sales" in data["scoped_departments"]

    def test_grant_non_department_admin_with_scopes_422(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        user = _seed_target_user(fake_persistence)
        resp = test_client.post(
            f"/api/v1/users/{user.id}/org-roles",
            json={"role": "owner", "scoped_departments": ["eng"]},
        )
        assert resp.status_code == 422

    def test_grant_duplicate_role_409(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        user = _seed_target_user(
            fake_persistence,
            org_roles=(OrgRole.EDITOR,),
        )
        resp = test_client.post(
            f"/api/v1/users/{user.id}/org-roles",
            json={"role": "editor"},
        )
        assert resp.status_code == 409

    def test_grant_to_nonexistent_user_404(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.post(
            "/api/v1/users/nonexistent-user/org-roles",
            json={"role": "editor"},
        )
        assert resp.status_code == 404


@pytest.mark.unit
class TestRevokeOrgRole:
    def test_revoke_editor_happy_path(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        user = _seed_target_user(
            fake_persistence,
            org_roles=(OrgRole.EDITOR,),
        )
        resp = test_client.delete(
            f"/api/v1/users/{user.id}/org-roles/editor",
        )
        assert resp.status_code == 204

    def test_revoke_last_owner_409(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        # App startup auto-promotes the first seeded user to OWNER.
        # Strip OWNER from all existing users so our target is the
        # sole owner.
        for uid, u in list(fake_persistence._users._users.items()):
            if OrgRole.OWNER in u.org_roles:
                fake_persistence._users._users[uid] = u.model_copy(
                    update={"org_roles": ()},
                )
        target = _seed_target_user(
            fake_persistence,
            user_id="sole-owner",
            username="sole-owner",
            org_roles=(OrgRole.OWNER,),
        )
        resp = test_client.delete(
            f"/api/v1/users/{target.id}/org-roles/owner",
        )
        assert resp.status_code == 409

    def test_revoke_owner_when_multiple_owners_exist(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        user1 = _seed_target_user(
            fake_persistence,
            user_id="owner-a",
            username="owner-a",
            org_roles=(OrgRole.OWNER,),
        )
        _seed_target_user(
            fake_persistence,
            user_id="owner-b",
            username="owner-b",
            org_roles=(OrgRole.OWNER,),
        )
        resp = test_client.delete(
            f"/api/v1/users/{user1.id}/org-roles/owner",
        )
        assert resp.status_code == 204

    def test_revoke_role_user_does_not_have_404(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        user = _seed_target_user(fake_persistence)
        resp = test_client.delete(
            f"/api/v1/users/{user.id}/org-roles/editor",
        )
        assert resp.status_code == 404

    def test_revoke_invalid_role_string_422(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        user = _seed_target_user(fake_persistence)
        resp = test_client.delete(
            f"/api/v1/users/{user.id}/org-roles/invalid_role",
        )
        assert resp.status_code == 422

    def test_revoke_from_nonexistent_user_404(
        self,
        test_client: TestClient[Any],
    ) -> None:
        resp = test_client.delete(
            "/api/v1/users/nonexistent-user/org-roles/editor",
        )
        assert resp.status_code == 404
