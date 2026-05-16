"""Parametrized conformance tests for UserRepository and ApiKeyRepository."""

from datetime import UTC, datetime

import pytest

from synthorg.core.auth.models import ApiKey, User
from synthorg.core.auth.roles import HumanRole
from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.user_protocol import ApiKeyFilterSpec, UserFilterSpec


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

    async def test_list_items_excludes_system(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.users.save(_make_user())
        users = await backend.users.list_items()
        assert len(users) >= 1
        assert all(u.role != HumanRole.SYSTEM for u in users)

    async def test_list_after_id_keyset_pages(
        self, backend: PersistenceBackend
    ) -> None:
        for idx in range(5):
            await backend.users.save(_make_user(f"user_{idx:02d}", f"name_{idx}"))
        first = await backend.users.list_after_id(after_id=None, limit=2)
        assert [u.id for u in first] == ["user_00", "user_01"]
        second = await backend.users.list_after_id(
            after_id=NotBlankStr("user_01"), limit=2
        )
        assert [u.id for u in second] == ["user_02", "user_03"]
        # Excludes the system user like list_items.
        assert all(u.role != HumanRole.SYSTEM for u in first + second)

    async def test_count(self, backend: PersistenceBackend) -> None:
        await backend.users.save(_make_user("u1", "one"))
        await backend.users.save(_make_user("u2", "two"))
        assert await backend.users.count(UserFilterSpec()) == 2

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

    async def test_query_by_user(self, backend: PersistenceBackend) -> None:
        await backend.users.save(_make_user())
        await backend.api_keys.save(_make_api_key("k1"))
        await backend.api_keys.save(_make_api_key("k2"))
        keys = await backend.api_keys.query(
            ApiKeyFilterSpec(user_id=NotBlankStr("user_alice"))
        )
        assert len(keys) == 2

    async def test_query_by_user_pagination(self, backend: PersistenceBackend) -> None:
        await backend.users.save(_make_user())
        for i in range(4):
            await backend.api_keys.save(_make_api_key(f"key_pg_{i}"))

        # Anchor pagination assertions against the actual sort order so
        # the test catches a window-shift regression even if the global
        # order changes.
        full = await backend.api_keys.query(
            ApiKeyFilterSpec(user_id=NotBlankStr("user_alice"))
        )
        full_ids = [k.id for k in full]
        assert {"key_pg_0", "key_pg_1", "key_pg_2", "key_pg_3"} <= set(full_ids)

        page = await backend.api_keys.query(
            ApiKeyFilterSpec(user_id=NotBlankStr("user_alice")),
            limit=2,
            offset=1,
        )
        assert [k.id for k in page] == full_ids[1:3]

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

    async def test_list_items_and_count(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.users.save(_make_user())
        for i in range(3):
            await backend.api_keys.save(_make_api_key(f"lk_{i}"))
        items = await backend.api_keys.list_items(limit=10)
        assert len({k.id for k in items}) >= 3
        total = await backend.api_keys.count(ApiKeyFilterSpec())
        assert total >= 3
        scoped = await backend.api_keys.count(
            ApiKeyFilterSpec(user_id=NotBlankStr("user_alice")),
        )
        assert scoped >= 3

    async def test_query_rejects_invalid_pagination(
        self, backend: PersistenceBackend
    ) -> None:
        with pytest.raises(QueryError):
            await backend.api_keys.query(ApiKeyFilterSpec(), limit=0)
        with pytest.raises(QueryError):
            await backend.api_keys.query(ApiKeyFilterSpec(), offset=-1)


@pytest.mark.integration
class TestUserRepositoryQueryCount:
    async def test_query_count_and_count_by_role(
        self, backend: PersistenceBackend
    ) -> None:
        await backend.users.save(
            _make_user(user_id="u_mgr", username="mgr", role=HumanRole.MANAGER),
        )
        await backend.users.save(
            _make_user(user_id="u_dev", username="dev", role=HumanRole.OBSERVER),
        )
        managers = await backend.users.query(
            UserFilterSpec(role=HumanRole.MANAGER),
        )
        assert all(u.role == HumanRole.MANAGER for u in managers)
        total = await backend.users.count(UserFilterSpec())
        assert total >= 2
        mgr_count = await backend.users.count_by_role(HumanRole.MANAGER)
        assert mgr_count >= 1

    async def test_query_rejects_invalid_pagination(
        self, backend: PersistenceBackend
    ) -> None:
        with pytest.raises(QueryError):
            await backend.users.query(UserFilterSpec(), limit=0)
        with pytest.raises(QueryError):
            await backend.users.query(UserFilterSpec(), offset=-1)
