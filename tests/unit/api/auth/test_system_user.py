"""Tests for the system user module."""

import pytest

from synthorg.api.auth.service import AuthService
from synthorg.api.auth.system_user import (
    SYSTEM_USER_ID,
    SYSTEM_USERNAME,
    ensure_system_user,
    is_system_user,
)
from synthorg.core.auth.config import AuthConfig
from synthorg.core.auth.roles import HumanRole
from synthorg.core.persistence_errors import QueryError
from tests.unit.api.conftest import _TEST_JWT_SECRET
from tests.unit.api.fakes import FakePersistenceBackend


def _make_auth_service() -> AuthService:
    return AuthService(AuthConfig(jwt_secret=_TEST_JWT_SECRET))


@pytest.fixture
def auth_svc() -> AuthService:
    return _make_auth_service()


@pytest.fixture
async def fake_persistence() -> FakePersistenceBackend:
    backend = FakePersistenceBackend()
    await backend.connect()
    return backend


@pytest.mark.unit
class TestIsSystemUser:
    @pytest.mark.parametrize(
        ("user_id", "expected"),
        [
            (SYSTEM_USER_ID, True),
            ("some-other-id", False),
            ("", False),
        ],
    )
    def test_is_system_user(self, user_id: str, expected: bool) -> None:
        assert is_system_user(user_id) is expected


@pytest.mark.unit
class TestEnsureSystemUser:
    async def test_creates_user_with_correct_attributes(
        self, auth_svc: AuthService, fake_persistence: FakePersistenceBackend
    ) -> None:
        await ensure_system_user(fake_persistence, auth_svc)

        user = await fake_persistence.users.get(SYSTEM_USER_ID)
        assert user is not None
        assert user.id == SYSTEM_USER_ID
        assert user.username == SYSTEM_USERNAME
        assert user.role == HumanRole.SYSTEM
        assert user.password_hash.startswith("$argon2id$")
        assert user.must_change_password is False

    async def test_idempotent(
        self, auth_svc: AuthService, fake_persistence: FakePersistenceBackend
    ) -> None:
        await ensure_system_user(fake_persistence, auth_svc)
        first = await fake_persistence.users.get(SYSTEM_USER_ID)

        await ensure_system_user(fake_persistence, auth_svc)
        second = await fake_persistence.users.get(SYSTEM_USER_ID)

        assert first is not None
        assert second is not None
        # Same object (not replaced) -- idempotent
        assert first.password_hash == second.password_hash

    async def test_excluded_from_count(
        self, auth_svc: AuthService, fake_persistence: FakePersistenceBackend
    ) -> None:
        await ensure_system_user(fake_persistence, auth_svc)

        # count() excludes system user
        assert await fake_persistence.users.count() == 0

    async def test_excluded_from_list_users(
        self, auth_svc: AuthService, fake_persistence: FakePersistenceBackend
    ) -> None:
        await ensure_system_user(fake_persistence, auth_svc)

        # list_users() excludes system user
        users = await fake_persistence.users.list_users()
        assert len(users) == 0

    async def test_persistence_error_propagates(
        self,
        auth_svc: AuthService,
        fake_persistence: FakePersistenceBackend,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        """Errors from persistence.users.save propagate to the caller.

        Uses ``monkeypatch.setattr`` so the original ``save`` is restored
        automatically after the test -- including when the assertion
        raises unexpectedly. A manual reassignment + teardown line
        would leave the fake in a broken state on failure and
        contaminate later tests in the same session.
        """

        async def _failing_save(user: object) -> None:
            msg = "simulated DB failure"
            raise QueryError(msg)

        monkeypatch.setattr(fake_persistence.users, "save", _failing_save)
        with pytest.raises(QueryError, match="simulated DB failure"):
            await ensure_system_user(fake_persistence, auth_svc)
