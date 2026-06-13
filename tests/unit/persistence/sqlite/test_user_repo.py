"""Tests for SQLiteUserRepository and SQLiteApiKeyRepository."""

from datetime import UTC, datetime
from unittest.mock import patch

import aiosqlite
import pytest

from synthorg.core.auth.models import ApiKey, User
from synthorg.core.auth.roles import HumanRole
from synthorg.core.persistence_errors import QueryError
from synthorg.persistence.sqlite.user_repo import (
    SQLiteApiKeyRepository,
    SQLiteUserRepository,
)
from synthorg.persistence.user_protocol import ApiKeyFilterSpec, UserFilterSpec
from tests._shared.persistence import make_private_write_context


@pytest.fixture
def db(migrated_db: aiosqlite.Connection) -> aiosqlite.Connection:
    """Alias for the shared migrated_db fixture."""
    return migrated_db


@pytest.fixture
def user_repo(db: aiosqlite.Connection) -> SQLiteUserRepository:
    return SQLiteUserRepository(db, write_context=make_private_write_context())


@pytest.fixture
def api_key_repo(db: aiosqlite.Connection) -> SQLiteApiKeyRepository:
    return SQLiteApiKeyRepository(db, write_context=make_private_write_context())


def _make_user(
    *,
    user_id: str = "user-001",
    username: str = "admin",
    role: HumanRole = HumanRole.MANAGER,
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

    async def test_naive_created_at_rejected(
        self,
        user_repo: SQLiteUserRepository,
        db: aiosqlite.Connection,
    ) -> None:
        """A stored naive timestamp is rejected on read, not silently coerced."""
        await user_repo.save(_make_user())
        await db.execute(
            "UPDATE users SET created_at = ? WHERE id = ?",
            ("2026-01-01T00:00:00", "user-001"),
        )
        await db.commit()
        with pytest.raises(QueryError):
            await user_repo.get("user-001")

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
        users = await user_repo.list_items()
        assert len(users) == 2

    async def test_count(self, user_repo: SQLiteUserRepository) -> None:
        assert await user_repo.count(UserFilterSpec()) == 0
        await user_repo.save(_make_user())
        assert await user_repo.count(UserFilterSpec()) == 1

    async def test_list_users_excludes_system_user(
        self, user_repo: SQLiteUserRepository
    ) -> None:
        await user_repo.save(_make_user(user_id="u1", username="alice"))
        await user_repo.save(
            _make_user(user_id="system", username="system", role=HumanRole.SYSTEM),
        )
        users = await user_repo.list_items()
        assert len(users) == 1
        assert users[0].id == "u1"

    async def test_count_excludes_system_user(
        self, user_repo: SQLiteUserRepository
    ) -> None:
        await user_repo.save(_make_user())
        await user_repo.save(
            _make_user(user_id="system", username="system", role=HumanRole.SYSTEM),
        )
        assert await user_repo.count(UserFilterSpec()) == 1

    async def test_delete_rejects_system_user(
        self, user_repo: SQLiteUserRepository
    ) -> None:
        await user_repo.save(
            _make_user(user_id="system", username="system", role=HumanRole.SYSTEM),
        )
        with pytest.raises(Exception, match="System user cannot be deleted"):
            await user_repo.delete("system")

    async def test_count_by_role_empty(self, user_repo: SQLiteUserRepository) -> None:
        assert await user_repo.count_by_role(HumanRole.CEO) == 0

    async def test_count_by_role_filters_correctly(
        self, user_repo: SQLiteUserRepository
    ) -> None:
        await user_repo.save(
            _make_user(user_id="mgr-1", username="alice", role=HumanRole.MANAGER),
        )
        await user_repo.save(
            _make_user(user_id="obs-1", username="bob", role=HumanRole.OBSERVER),
        )
        await user_repo.save(
            _make_user(user_id="mgr-2", username="carol", role=HumanRole.MANAGER),
        )

        assert await user_repo.count_by_role(HumanRole.MANAGER) == 2
        assert await user_repo.count_by_role(HumanRole.OBSERVER) == 1
        assert await user_repo.count_by_role(HumanRole.CEO) == 0

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
        assert await user_repo.count(UserFilterSpec()) == 1


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
        keys = await api_key_repo.query(ApiKeyFilterSpec(user_id="user-001"))
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


# ── Logger contract on persistence error paths ──────────────────


@pytest.mark.unit
class TestSec1LoggerContract:
    """Pin the logger contract for persistence error paths.

    Persistence repos catch ``aiosqlite.Error`` / ``sqlite3.Error``
    and log at WARNING with ``error_type=type(exc).__name__`` plus
    ``error=safe_error_description(exc)``. A regression to
    ``logger.exception(EVENT, error=str(exc))`` would re-introduce
    the credential leak (postgres/sqlite connection strings, SQL
    fragments) the pre-commit gate already blocks at the AST level.
    This test pins the runtime contract so a CI-only mistake (e.g.,
    a flag flip on the gate) cannot mask it.
    """

    async def test_get_db_error_logs_warning(
        self,
        user_repo: SQLiteUserRepository,
    ) -> None:
        """A DB error during ``get`` logs at WARNING with redacted error."""
        with (
            patch.object(
                user_repo._db,
                "execute",
                side_effect=aiosqlite.Error("connection lost"),
            ),
            patch(
                "synthorg.persistence.sqlite.user_repo.logger",
            ) as mock_logger,
            pytest.raises(QueryError),
        ):
            await user_repo.get("user-001")
        mock_logger.warning.assert_called_once()
        call = mock_logger.warning.call_args
        # Positional EVENT constant is preserved.
        assert call.args, "expected EVENT constant as first positional arg"
        # Structured kwargs follow the safe-redaction shape.
        assert call.kwargs.get("error_type") == "Error"
        assert "error" in call.kwargs
        # Crucially: the error value must be the safe_error_description
        # output (prefixed with the type name), not raw str(exc).
        assert call.kwargs["error"].startswith("Error:")
        # And logger.exception must NOT have been called.
        mock_logger.exception.assert_not_called()

    async def test_get_db_error_scrubs_connection_string(
        self,
        user_repo: SQLiteUserRepository,
    ) -> None:
        """Postgres-style URI userinfo in a DB error message is scrubbed.

        End-to-end check: a credential-bearing exception message
        flows through ``logger.warning`` +
        ``safe_error_description`` and the captured log value has
        the password masked. This pins the helper's scrub contract
        at the persistence boundary, covering the scenario the
        ``logger.exception(EVENT, error=str(exc))`` pattern leaked.
        """
        leaky_message = (
            "could not connect: postgres://app_user:hunter2@db.internal:5432/app"
        )
        with (
            patch.object(
                user_repo._db,
                "execute",
                side_effect=aiosqlite.Error(leaky_message),
            ),
            patch(
                "synthorg.persistence.sqlite.user_repo.logger",
            ) as mock_logger,
            pytest.raises(QueryError),
        ):
            await user_repo.get("user-001")
        call = mock_logger.warning.call_args
        logged_error = call.kwargs["error"]
        # Password is masked, but scheme + host survive for triage.
        assert "hunter2" not in logged_error
        assert "***@db.internal" in logged_error
        assert "postgres://" in logged_error
