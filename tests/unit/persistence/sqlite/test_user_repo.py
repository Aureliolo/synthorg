"""Tests for SQLiteUserRepository and SQLiteApiKeyRepository."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import aiosqlite
import pytest

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

from synthorg.api.auth.models import ApiKey, User
from synthorg.api.guards import HumanRole
from synthorg.persistence.sqlite.migrations import run_migrations
from synthorg.persistence.sqlite.user_repo import (
    SQLiteApiKeyRepository,
    SQLiteUserRepository,
)


@pytest.fixture
async def db() -> AsyncGenerator[aiosqlite.Connection]:
    """Create an in-memory SQLite DB with schema applied."""
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await run_migrations(conn)
    yield conn
    await conn.close()


@pytest.fixture
def user_repo(db: aiosqlite.Connection) -> SQLiteUserRepository:
    return SQLiteUserRepository(db)


@pytest.fixture
def api_key_repo(db: aiosqlite.Connection) -> SQLiteApiKeyRepository:
    return SQLiteApiKeyRepository(db)


def _make_user(
    *,
    user_id: str = "user-001",
    username: str = "admin",
    role: HumanRole = HumanRole.CEO,
) -> User:
    now = datetime.now(UTC)
    return User(
        id=user_id,
        username=username,
        password_hash="$argon2id$fake-hash",
        role=role,
        must_change_password=False,
        created_at=now,
        updated_at=now,
    )


@pytest.mark.unit
class TestSQLiteUserRepository:
    async def test_save_and_get(self, user_repo: SQLiteUserRepository) -> None:
        user = _make_user()
        await user_repo.save(user)
        fetched = await user_repo.get("user-001")
        assert fetched is not None
        assert fetched.id == "user-001"
        assert fetched.username == "admin"

    async def test_get_nonexistent(self, user_repo: SQLiteUserRepository) -> None:
        result = await user_repo.get("nonexistent")
        assert result is None

    async def test_get_by_username(self, user_repo: SQLiteUserRepository) -> None:
        user = _make_user()
        await user_repo.save(user)
        fetched = await user_repo.get_by_username("admin")
        assert fetched is not None
        assert fetched.id == "user-001"

    async def test_get_by_username_not_found(
        self, user_repo: SQLiteUserRepository
    ) -> None:
        result = await user_repo.get_by_username("nope")
        assert result is None

    async def test_list_users(self, user_repo: SQLiteUserRepository) -> None:
        await user_repo.save(_make_user(user_id="u1", username="alice"))
        await user_repo.save(_make_user(user_id="u2", username="bob"))
        users = await user_repo.list_users()
        assert len(users) == 2

    async def test_count(self, user_repo: SQLiteUserRepository) -> None:
        assert await user_repo.count() == 0
        await user_repo.save(_make_user())
        assert await user_repo.count() == 1

    async def test_delete(self, user_repo: SQLiteUserRepository) -> None:
        await user_repo.save(_make_user())
        deleted = await user_repo.delete("user-001")
        assert deleted is True
        assert await user_repo.get("user-001") is None

    async def test_delete_nonexistent(self, user_repo: SQLiteUserRepository) -> None:
        deleted = await user_repo.delete("nope")
        assert deleted is False

    async def test_upsert(self, user_repo: SQLiteUserRepository) -> None:
        user = _make_user()
        await user_repo.save(user)
        updated = user.model_copy(
            update={"username": "new-admin", "updated_at": datetime.now(UTC)}
        )
        await user_repo.save(updated)
        fetched = await user_repo.get("user-001")
        assert fetched is not None
        assert fetched.username == "new-admin"
        assert await user_repo.count() == 1


@pytest.mark.unit
class TestSQLiteApiKeyRepository:
    async def test_save_and_get(
        self,
        api_key_repo: SQLiteApiKeyRepository,
        user_repo: SQLiteUserRepository,
    ) -> None:
        await user_repo.save(_make_user())
        now = datetime.now(UTC)
        key = ApiKey(
            id="key-001",
            key_hash="abc123hash",
            name="test-key",
            role=HumanRole.CEO,
            user_id="user-001",
            created_at=now,
        )
        await api_key_repo.save(key)
        fetched = await api_key_repo.get("key-001")
        assert fetched is not None
        assert fetched.name == "test-key"

    async def test_get_by_hash(
        self,
        api_key_repo: SQLiteApiKeyRepository,
        user_repo: SQLiteUserRepository,
    ) -> None:
        await user_repo.save(_make_user())
        now = datetime.now(UTC)
        key = ApiKey(
            id="key-002",
            key_hash="unique-hash",
            name="hash-key",
            role=HumanRole.CEO,
            user_id="user-001",
            created_at=now,
        )
        await api_key_repo.save(key)
        fetched = await api_key_repo.get_by_hash("unique-hash")
        assert fetched is not None
        assert fetched.id == "key-002"

    async def test_list_by_user(
        self,
        api_key_repo: SQLiteApiKeyRepository,
        user_repo: SQLiteUserRepository,
    ) -> None:
        await user_repo.save(_make_user())
        now = datetime.now(UTC)
        for i in range(3):
            key = ApiKey(
                id=f"key-{i}",
                key_hash=f"hash-{i}",
                name=f"key-{i}",
                role=HumanRole.CEO,
                user_id="user-001",
                created_at=now,
            )
            await api_key_repo.save(key)
        keys = await api_key_repo.list_by_user("user-001")
        assert len(keys) == 3

    async def test_delete(
        self,
        api_key_repo: SQLiteApiKeyRepository,
        user_repo: SQLiteUserRepository,
    ) -> None:
        await user_repo.save(_make_user())
        now = datetime.now(UTC)
        key = ApiKey(
            id="key-del",
            key_hash="del-hash",
            name="del-key",
            role=HumanRole.CEO,
            user_id="user-001",
            created_at=now,
        )
        await api_key_repo.save(key)
        assert await api_key_repo.delete("key-del") is True
        assert await api_key_repo.get("key-del") is None
