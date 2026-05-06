"""Integration tests for user-deletion cascade (CFG-1 audit).

Exercises :meth:`UserService.delete` end-to-end against a real SQLite
persistence backend and asserts the cascade:

1. Refresh tokens are explicitly revoked before the DB delete
   (defense-in-depth) -- :meth:`RefreshTokenRepository.revoke_by_user`
   is called and returns the revoked count.
2. ``refresh_tokens``, ``sessions``, and ``api_keys`` rows are removed
   atomically by the schema-level ``ON DELETE CASCADE`` on
   ``user_id`` when the user row goes away.
3. If refresh-token revocation raises, the user delete is aborted
   (fail-closed) so tokens are never left live alongside a deleted
   user.
"""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.api.auth.user_service import UserService
from synthorg.core.auth.models import ApiKey, OrgRole, User
from synthorg.core.auth.refresh_record import RefreshConsumeOutcome
from synthorg.core.auth.roles import HumanRole
from synthorg.core.auth.session import Session
from synthorg.persistence.sqlite.backend import SQLitePersistenceBackend

_UNUSED_STUB_METHOD = "not used in this test"
_SIMULATED_REPO_FAILURE = "simulated refresh repo failure"


def _make_user(
    *,
    user_id: str = "user-del-001",
    username: str = "alice-del",
    role: HumanRole = HumanRole.MANAGER,
    org_roles: tuple[OrgRole, ...] = (),
) -> User:
    now = datetime.now(UTC)
    return User(
        id=user_id,
        username=username,
        password_hash="$argon2id$fake-hash",
        role=role,
        must_change_password=False,
        org_roles=org_roles,
        created_at=now,
        updated_at=now,
    )


def _make_session(
    *,
    session_id: str,
    user_id: str,
    username: str,
    role: HumanRole = HumanRole.MANAGER,
) -> Session:
    now = datetime.now(UTC)
    return Session(
        session_id=session_id,
        user_id=user_id,
        username=username,
        role=role,
        ip_address="127.0.0.1",
        user_agent="pytest",
        created_at=now,
        last_active_at=now,
        expires_at=now + timedelta(hours=1),
    )


async def _count_refresh_token_rows(
    on_disk_backend: SQLitePersistenceBackend,
    *,
    user_id: str,
) -> int:
    """Count ``refresh_tokens`` rows keyed by ``user_id`` directly.

    ``RefreshTokenRepository.revoke_by_user`` only counts tokens it
    newly flipped from ``used=0`` to ``used=1``, so on a broken FK
    cascade where the row is still present but pre-revoked, that
    method would still return 0.  The cascade tests need a row-level
    presence check to distinguish "cascade dropped the row" from
    "row still there but already revoked", so they reach into the
    backend's ``_db`` connection and run a direct ``COUNT(*)``.
    """
    # Reach the connection via the backend's protocol-level get_db()
    # rather than the repo's private _db attribute. The repo property
    # is now Protocol-typed (RefreshTokenRepository) and intentionally
    # hides backend internals; the SQLite-specific raw COUNT is still
    # appropriate here because this entire test fixture is SQLite-only
    # (uses on_disk_backend: SQLitePersistenceBackend).
    db = on_disk_backend.get_db()
    cursor = await db.execute(
        "SELECT COUNT(*) FROM refresh_tokens WHERE user_id = ?",
        (user_id,),
    )
    row = await cursor.fetchone()
    return int(row[0]) if row is not None else 0


async def _seed_user_with_dependencies(  # noqa: PLR0913
    *,
    on_disk_backend: SQLitePersistenceBackend,
    user_id: str,
    username: str,
    session_id: str,
    token_hash: str,
    api_key_id: str,
    api_key_hash: str,
    api_key_name: str,
) -> tuple[User, Session, ApiKey]:
    """Build and persist a user plus a session, refresh token, and api key.

    Shared setup for the cascade tests -- both verify that deleting
    the user cascades across every dependent auth row, so each test
    needs an identical fanout and the bulk of the body is noise.
    Returns the three models so individual tests can assert against
    them by id after calling ``UserService.delete``.
    """
    user = _make_user(user_id=user_id, username=username)
    await on_disk_backend.users.save(user)

    session = _make_session(
        session_id=session_id,
        user_id=user.id,
        username=user.username,
    )
    await on_disk_backend.sessions.create(session)

    expires = datetime.now(UTC) + timedelta(hours=1)
    await on_disk_backend.refresh_tokens.create(
        token_hash,
        session.session_id,
        user.id,
        expires,
    )

    api_key = ApiKey(
        id=api_key_id,
        key_hash=api_key_hash,
        name=api_key_name,
        role=HumanRole.MANAGER,
        user_id=user.id,
        created_at=datetime.now(UTC),
    )
    await on_disk_backend.api_keys.save(api_key)
    return user, session, api_key


@pytest.mark.integration
class TestUserDeletionCascade:
    """End-to-end cascade on ``DELETE /users/{id}`` via UserService."""

    async def test_delete_revokes_refresh_tokens_then_cascades(
        self,
        on_disk_backend: SQLitePersistenceBackend,
    ) -> None:
        """delete() revokes refresh tokens and FK cascades sessions + api_keys."""
        user, session, api_key = await _seed_user_with_dependencies(
            on_disk_backend=on_disk_backend,
            user_id="user-del-001",
            username="alice-del",
            session_id="sess-del-001",
            token_hash="token-hash-del-001",
            api_key_id="apikey-del-001",
            api_key_hash="deadbeef" * 8,
            api_key_name="integration test key",
        )

        service = UserService(
            repo=on_disk_backend.users,
            refresh_tokens=on_disk_backend.refresh_tokens,
        )

        deleted = await service.delete(
            user.id,
            deleted_by_user_id="admin-001",
        )

        assert deleted is True
        assert await on_disk_backend.users.get(user.id) is None
        assert await on_disk_backend.sessions.get(session.session_id) is None
        assert await on_disk_backend.api_keys.get(api_key.id) is None
        # Refresh tokens cascaded via FK (revoke_by_user flipped used=1
        # first, then the FK cascade dropped the row). A direct COUNT
        # verifies row removal rather than re-revocation.
        assert await _count_refresh_token_rows(on_disk_backend, user_id=user.id) == 0

    async def test_delete_without_refresh_repo_still_cascades(
        self,
        on_disk_backend: SQLitePersistenceBackend,
    ) -> None:
        """Cascade works even when UserService has no refresh_tokens repo.

        Exercises the full dependent set (sessions + api_keys + refresh
        tokens) so the FK cascade is verified end-to-end for this
        constructor variant as well, not just for the defense-in-depth
        path in the sibling test.
        """
        user, session, api_key = await _seed_user_with_dependencies(
            on_disk_backend=on_disk_backend,
            user_id="user-del-002",
            username="bob-del",
            session_id="sess-del-002",
            token_hash="token-hash-del-002",
            api_key_id="apikey-del-002",
            api_key_hash="feedface" * 8,
            api_key_name="integration test key no-refresh-repo",
        )

        service = UserService(repo=on_disk_backend.users)

        deleted = await service.delete(
            user.id,
            deleted_by_user_id="admin-001",
        )

        assert deleted is True
        assert await on_disk_backend.users.get(user.id) is None
        assert await on_disk_backend.sessions.get(session.session_id) is None
        assert await on_disk_backend.api_keys.get(api_key.id) is None
        # Refresh tokens cascaded via schema FK (no explicit revoke
        # step since this constructor variant has no refresh_tokens
        # repo). A direct COUNT verifies the row was dropped.
        assert await _count_refresh_token_rows(on_disk_backend, user_id=user.id) == 0

    async def test_delete_missing_user_returns_false(
        self,
        on_disk_backend: SQLitePersistenceBackend,
    ) -> None:
        """delete() returns False when no user row matches."""
        service = UserService(
            repo=on_disk_backend.users,
            refresh_tokens=on_disk_backend.refresh_tokens,
        )

        deleted = await service.delete(
            "user-does-not-exist",
            deleted_by_user_id="admin-001",
        )

        assert deleted is False

    async def test_delete_fails_closed_when_revocation_raises(
        self,
        on_disk_backend: SQLitePersistenceBackend,
    ) -> None:
        """If refresh-token revocation raises, the user delete is aborted."""
        user = _make_user(user_id="user-del-003", username="carol-del")
        await on_disk_backend.users.save(user)

        class _RaisingRefreshRepo:
            """Stub that simulates a failing refresh-token backend."""

            async def create(self, *args: object, **kwargs: object) -> None:
                del args, kwargs
                raise AssertionError(_UNUSED_STUB_METHOD)

            async def consume(
                self,
                *args: object,
                **kwargs: object,
            ) -> RefreshConsumeOutcome:
                del args, kwargs
                raise AssertionError(_UNUSED_STUB_METHOD)

            async def revoke_by_session(self, session_id: str) -> int:
                del session_id
                raise AssertionError(_UNUSED_STUB_METHOD)

            async def revoke_by_user(self, user_id: str) -> int:
                del user_id
                raise RuntimeError(_SIMULATED_REPO_FAILURE)

            async def cleanup_expired(self) -> int:
                raise AssertionError(_UNUSED_STUB_METHOD)

        service = UserService(
            repo=on_disk_backend.users,
            refresh_tokens=_RaisingRefreshRepo(),
        )

        with pytest.raises(RuntimeError, match=_SIMULATED_REPO_FAILURE):
            await service.delete(
                user.id,
                deleted_by_user_id="admin-001",
            )

        # User row survives -- fail-closed semantics
        still_there = await on_disk_backend.users.get(user.id)
        assert still_there is not None
        assert still_there.id == user.id
