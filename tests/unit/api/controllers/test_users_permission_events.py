"""Permission grant/revoke emit dedicated audit-chain events.

Work package #1883 adds ``SECURITY_PERMISSION_GRANTED`` and
``SECURITY_PERMISSION_REVOKED`` so forensic readers can filter every
permission change by event constant alone. The existing
``SECURITY_USER_UPDATED`` event still fires (it carries the full user
row); the dedicated events are additive coverage for permission deltas.
"""

from datetime import UTC, datetime
from typing import Any

import pytest
import structlog.testing
from litestar.testing import TestClient

from synthorg.core.auth.models import OrgRole, User
from synthorg.core.auth.roles import HumanRole
from synthorg.observability.events.security import (
    SECURITY_PERMISSION_GRANTED,
    SECURITY_PERMISSION_REVOKED,
    SECURITY_USER_UPDATED,
)
from tests.unit.api.fakes import FakePersistenceBackend


def _seed_target_user(  # noqa: PLR0913 -- six fixture kwargs are all defaults
    fake_persistence: FakePersistenceBackend,
    *,
    user_id: str = "target-user-perm",
    username: str = "target-perm",
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
class TestGrantOrgRoleAuditEvents:
    """Granting an org role emits the dedicated permission event."""

    def test_grant_emits_permission_granted(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        user = _seed_target_user(fake_persistence)
        with structlog.testing.capture_logs() as logs:
            resp = test_client.post(
                f"/api/v1/users/{user.id}/org-roles",
                json={"role": "editor"},
            )
        assert resp.status_code == 201
        granted = [e for e in logs if e.get("event") == SECURITY_PERMISSION_GRANTED]
        assert len(granted) == 1
        assert granted[0]["user_id"] == user.id
        assert granted[0]["role"] == "editor"
        assert granted[0]["scoped_departments"] == ()

    def test_grant_also_emits_legacy_user_updated(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        """Additive coverage: SECURITY_USER_UPDATED keeps firing."""
        user = _seed_target_user(fake_persistence)
        with structlog.testing.capture_logs() as logs:
            test_client.post(
                f"/api/v1/users/{user.id}/org-roles",
                json={"role": "editor"},
            )
        updated = [e for e in logs if e.get("event") == SECURITY_USER_UPDATED]
        assert len(updated) == 1
        assert updated[0]["intent"] == "grant_org_role"
        assert updated[0]["granted_org_role"] == "editor"

    def test_grant_department_admin_carries_scoped_departments(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        user = _seed_target_user(fake_persistence)
        with structlog.testing.capture_logs() as logs:
            resp = test_client.post(
                f"/api/v1/users/{user.id}/org-roles",
                json={
                    "role": "department_admin",
                    "scoped_departments": ["eng", "ops"],
                },
            )
        assert resp.status_code == 201
        granted = [e for e in logs if e.get("event") == SECURITY_PERMISSION_GRANTED]
        assert len(granted) == 1
        assert granted[0]["role"] == "department_admin"
        assert set(granted[0]["scoped_departments"]) == {"eng", "ops"}


@pytest.mark.unit
class TestRevokeOrgRoleAuditEvents:
    """Revoking an org role emits the dedicated permission event."""

    def test_revoke_emits_permission_revoked(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        user = _seed_target_user(
            fake_persistence,
            org_roles=(OrgRole.EDITOR,),
        )
        with structlog.testing.capture_logs() as logs:
            resp = test_client.delete(
                f"/api/v1/users/{user.id}/org-roles/editor",
            )
        assert resp.status_code == 204
        revoked = [e for e in logs if e.get("event") == SECURITY_PERMISSION_REVOKED]
        assert len(revoked) == 1
        assert revoked[0]["user_id"] == user.id
        assert revoked[0]["role"] == "editor"

    def test_revoke_also_emits_legacy_user_updated(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        """Additive coverage: SECURITY_USER_UPDATED keeps firing."""
        user = _seed_target_user(
            fake_persistence,
            org_roles=(OrgRole.EDITOR,),
        )
        with structlog.testing.capture_logs() as logs:
            test_client.delete(
                f"/api/v1/users/{user.id}/org-roles/editor",
            )
        updated = [e for e in logs if e.get("event") == SECURITY_USER_UPDATED]
        assert len(updated) == 1
        assert updated[0]["intent"] == "revoke_org_role"
        assert updated[0]["revoked_org_role"] == "editor"

    def test_revoke_missing_role_does_not_emit_permission_event(
        self,
        test_client: TestClient[Any],
        fake_persistence: FakePersistenceBackend,
    ) -> None:
        """If the user does not hold the role, no permission event fires."""
        user = _seed_target_user(fake_persistence)  # no org_roles
        with structlog.testing.capture_logs() as logs:
            resp = test_client.delete(
                f"/api/v1/users/{user.id}/org-roles/editor",
            )
        assert resp.status_code == 404
        revoked = [e for e in logs if e.get("event") == SECURITY_PERMISSION_REVOKED]
        assert len(revoked) == 0


@pytest.mark.unit
class TestSecurityEventConstants:
    """Pin the wire values so the audit-chain sink registry catches typos."""

    def test_granted_constant(self) -> None:
        assert SECURITY_PERMISSION_GRANTED == "security.permission.granted"

    def test_revoked_constant(self) -> None:
        assert SECURITY_PERMISSION_REVOKED == "security.permission.revoked"
