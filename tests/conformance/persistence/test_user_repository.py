"""Parametrized conformance tests for UserRepository and ApiKeyRepository."""

from datetime import UTC, datetime

import pytest

from synthorg.api.auth.models import ApiKey, User
from synthorg.api.guards import HumanRole
from synthorg.core.types import NotBlankStr
from synthorg.persistence.errors import QueryError
from synthorg.persistence.protocol import PersistenceBackend


def _make_user(
    user_id: str = "user_alice",
    username: str = "alice",
    role: HumanRole = HumanRole.MANAGER,
) -> User:
    now = datetime(2026, 4, 10, 12, tzinfo=UTC)
    return User(
        id=NotBlankStr(user_id),
        username=NotBlankStr(username),
        password_hash=NotBlankStr("$argon2id$v=19$m=65536,t=3,p=4$cGVwcGVy$abcd1234"),
        role=role,
        must_change_password=True,
        org_roles=(),
        scoped_departments=(),
        created_at=now,
        updated_at=now,
    )


def _make_api_key(
    key_id: str = "key_1",
    user_id: str = "user_alice",
    key_hash: str | None = None,
) -> ApiKey:
    now = datetime(2026, 4, 10, 12, tzinfo=UTC)
    return ApiKey(
        id=NotBlankStr(key_id),
        key_hash=NotBlankStr(key_hash or f"hash_{key_id}_{'0' * 50}"[:64]),
        name=NotBlankStr("primary"),
        role=HumanRole.MANAGER,
        user_id=NotBlankStr(user_id),
        created_at=now,
        expires_at=None,
        revoked=False,
    )


@pytest.mark.integration
class TestUserRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        user = _make_user()
        await backend.users.save(user)
        fetched = await backend.users.get(NotBlankStr("user_alice"))
        assert fetched is not None
        assert fetched.username == "alice"
        assert fetched.role == HumanRole.MANAGER
        assert fetched.must_change_password is True

    async def test_get_missing_returns_none(self, backend: PersistenceBackend) -> None:
        assert await backend.users.get(NotBlankStr("missing")) is None

    async def test_get_by_username(self, backend: PersistenceBackend) -> None:
        await backend.users.save(_make_user(username="bob", user_id="user_bob"))
        fetched = await backend.users.get_by_username(NotBlankStr("bob"))
        assert fetched is not None
        assert fetched.id == "user_bob"

    async def test_upsert_updates_existing(self, backend: PersistenceBackend) -> None:
        user = _make_user()
        await backend.users.save(user)
        updated = user.model_copy(update={"username": NotBlankStr("alice_new")})
        await backend.users.save(updated)
        fetched = await backend.users.get(NotBlankStr("user_alice"))
        assert fetched is not None
        assert fetched.username == "alice_new"

    async def test_list_users_excludes_system(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.users.save(_make_user())
        users = await backend.users.list_users()
        assert len(users) >= 1
        assert all(u.role != HumanRole.SYSTEM for u in users)

    async def test_count(self, backend: PersistenceBackend) -> None:
        await backend.users.save(_make_user("u1", "one"))
        await backend.users.save(_make_user("u2", "two"))
        assert await backend.users.count() == 2

    async def test_count_by_role(self, backend: PersistenceBackend) -> None:
        await backend.users.save(_make_user("u1", "one", HumanRole.MANAGER))
        await backend.users.save(_make_user("u2", "two", HumanRole.MANAGER))
        await backend.users.save(_make_user("u3", "three", HumanRole.CEO))
        assert await backend.users.count_by_role(HumanRole.MANAGER) == 2
        assert await backend.users.count_by_role(HumanRole.CEO) == 1

    async def test_delete_returns_true_when_present(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.users.save(_make_user())
        assert await backend.users.delete(NotBlankStr("user_alice")) is True
        assert await backend.users.get(NotBlankStr("user_alice")) is None

    async def test_delete_returns_false_when_missing(
        self, backend: PersistenceBackend
    ) -> None:
        assert await backend.users.delete(NotBlankStr("missing")) is False

    async def test_delete_system_user_raises(self, backend: PersistenceBackend) -> None:
        with pytest.raises(QueryError, match="System user"):
            await backend.users.delete(NotBlankStr("system"))


@pytest.mark.integration
class TestApiKeyRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        await backend.users.save(_make_user())
        key = _make_api_key()
        await backend.api_keys.save(key)
        fetched = await backend.api_keys.get(NotBlankStr("key_1"))
        assert fetched is not None
        assert fetched.name == "primary"
        assert fetched.revoked is False

    async def test_get_by_hash(self, backend: PersistenceBackend) -> None:
        await backend.users.save(_make_user())
        key = _make_api_key()
        await backend.api_keys.save(key)
        fetched = await backend.api_keys.get_by_hash(key.key_hash)
        assert fetched is not None
        assert fetched.id == "key_1"

    async def test_list_by_user(self, backend: PersistenceBackend) -> None:
        await backend.users.save(_make_user())
        await backend.api_keys.save(_make_api_key("k1"))
        await backend.api_keys.save(_make_api_key("k2"))
        keys = await backend.api_keys.list_by_user(NotBlankStr("user_alice"))
        assert len(keys) == 2

    async def test_list_by_user_pagination(self, backend: PersistenceBackend) -> None:
        await backend.users.save(_make_user())
        for i in range(4):
            await backend.api_keys.save(_make_api_key(f"key_pg_{i}"))

        page = await backend.api_keys.list_by_user(
            NotBlankStr("user_alice"),
            limit=2,
            offset=1,
        )

        # ORDER BY created_at; per-test database means only our 4 rows
        # exist, so offset=1 limit=2 yields the second + third rows.
        assert len(page) == 2

    async def test_delete(self, backend: PersistenceBackend) -> None:
        await backend.users.save(_make_user())
        await backend.api_keys.save(_make_api_key())
        assert await backend.api_keys.delete(NotBlankStr("key_1")) is True
        assert await backend.api_keys.get(NotBlankStr("key_1")) is None

    async def test_upsert_updates_existing(self, backend: PersistenceBackend) -> None:
        await backend.users.save(_make_user())
        key = _make_api_key()
        await backend.api_keys.save(key)
        revoked = key.model_copy(update={"revoked": True})
        await backend.api_keys.save(revoked)
        fetched = await backend.api_keys.get(NotBlankStr("key_1"))
        assert fetched is not None
        assert fetched.revoked is True
