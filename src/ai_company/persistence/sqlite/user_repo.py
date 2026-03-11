"""SQLite repository implementations for User and ApiKey.

Provides ``SQLiteUserRepository`` and ``SQLiteApiKeyRepository``, which
persist ``User`` and ``ApiKey`` domain models to SQLite via aiosqlite.
Both use upsert semantics for ``save`` operations.
"""

import sqlite3
from datetime import UTC, datetime

import aiosqlite
from pydantic import ValidationError

from ai_company.api.auth.models import ApiKey, User
from ai_company.api.guards import HumanRole
from ai_company.core.types import NotBlankStr  # noqa: TC001
from ai_company.observability import get_logger
from ai_company.observability.events.persistence import (
    PERSISTENCE_API_KEY_DELETE_FAILED,
    PERSISTENCE_API_KEY_DELETED,
    PERSISTENCE_API_KEY_FETCH_FAILED,
    PERSISTENCE_API_KEY_FETCHED,
    PERSISTENCE_API_KEY_LIST_FAILED,
    PERSISTENCE_API_KEY_LISTED,
    PERSISTENCE_API_KEY_SAVE_FAILED,
    PERSISTENCE_API_KEY_SAVED,
    PERSISTENCE_USER_COUNT_FAILED,
    PERSISTENCE_USER_COUNTED,
    PERSISTENCE_USER_DELETE_FAILED,
    PERSISTENCE_USER_DELETED,
    PERSISTENCE_USER_FETCH_FAILED,
    PERSISTENCE_USER_FETCHED,
    PERSISTENCE_USER_LIST_FAILED,
    PERSISTENCE_USER_LISTED,
    PERSISTENCE_USER_SAVE_FAILED,
    PERSISTENCE_USER_SAVED,
)
from ai_company.persistence.errors import QueryError

logger = get_logger(__name__)


def _row_to_user(row: aiosqlite.Row) -> User:
    """Reconstruct a ``User`` from a database row.

    Converts SQLite-native types (integers, ISO strings) back into
    the domain model's expected Python types.

    Args:
        row: A single database row with user columns.

    Returns:
        Validated ``User`` model instance.
    """
    data = dict(row)
    data["must_change_password"] = bool(data["must_change_password"])
    data["role"] = HumanRole(data["role"])
    data["created_at"] = datetime.fromisoformat(data["created_at"])
    data["updated_at"] = datetime.fromisoformat(data["updated_at"])
    return User.model_validate(data)


def _row_to_api_key(row: aiosqlite.Row) -> ApiKey:
    """Reconstruct an ``ApiKey`` from a database row.

    Converts SQLite-native types (integers, ISO strings) back into
    the domain model's expected Python types.

    Args:
        row: A single database row with API key columns.

    Returns:
        Validated ``ApiKey`` model instance.
    """
    data = dict(row)
    data["revoked"] = bool(data["revoked"])
    data["role"] = HumanRole(data["role"])
    data["created_at"] = datetime.fromisoformat(data["created_at"])
    if data["expires_at"] is not None:
        data["expires_at"] = datetime.fromisoformat(data["expires_at"])
    return ApiKey.model_validate(data)


class SQLiteUserRepository:
    """SQLite-backed user repository.

    Provides CRUD operations for ``User`` models using a shared
    ``aiosqlite.Connection``.  All write operations commit
    immediately.

    Args:
        db: An open aiosqlite connection with ``row_factory``
            set to ``aiosqlite.Row``.
    """

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    async def save(self, user: User) -> None:
        """Persist a user via upsert (insert or update on conflict).

        Args:
            user: User model to persist.

        Raises:
            QueryError: If the database operation fails.
        """
        try:
            await self._db.execute(
                """\
INSERT INTO users (id, username, password_hash, role,
                   must_change_password, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
    username=excluded.username,
    password_hash=excluded.password_hash,
    role=excluded.role,
    must_change_password=excluded.must_change_password,
    updated_at=excluded.updated_at""",
                (
                    user.id,
                    user.username,
                    user.password_hash,
                    user.role.value,
                    int(user.must_change_password),
                    user.created_at.astimezone(UTC).isoformat(),
                    user.updated_at.astimezone(UTC).isoformat(),
                ),
            )
            await self._db.commit()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to save user {user.id!r}"
            logger.exception(
                PERSISTENCE_USER_SAVE_FAILED,
                user_id=user.id,
                error=str(exc),
            )
            raise QueryError(msg) from exc
        logger.info(PERSISTENCE_USER_SAVED, user_id=user.id)

    async def get(self, user_id: NotBlankStr) -> User | None:
        """Retrieve a user by primary key.

        Args:
            user_id: Unique user identifier.

        Returns:
            The matching ``User``, or ``None`` if not found.

        Raises:
            QueryError: If the database query or deserialization fails.
        """
        try:
            cursor = await self._db.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to fetch user {user_id!r}"
            logger.exception(
                PERSISTENCE_USER_FETCH_FAILED,
                user_id=user_id,
                error=str(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            logger.debug(PERSISTENCE_USER_FETCHED, user_id=user_id, found=False)
            return None
        try:
            user = _row_to_user(row)
        except (ValueError, ValidationError) as exc:
            msg = f"Failed to deserialize user {user_id!r}"
            logger.exception(
                PERSISTENCE_USER_FETCH_FAILED,
                user_id=user_id,
                error=str(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_USER_FETCHED, user_id=user_id, found=True)
        return user

    async def get_by_username(self, username: NotBlankStr) -> User | None:
        """Retrieve a user by their unique username.

        Args:
            username: Login username to look up.

        Returns:
            The matching ``User``, or ``None`` if not found.

        Raises:
            QueryError: If the database query or deserialization fails.
        """
        try:
            cursor = await self._db.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to fetch user by username {username!r}"
            logger.exception(
                PERSISTENCE_USER_FETCH_FAILED,
                username=username,
                error=str(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        try:
            return _row_to_user(row)
        except (ValueError, ValidationError) as exc:
            msg = f"Failed to deserialize user {username!r}"
            logger.exception(
                PERSISTENCE_USER_FETCH_FAILED,
                username=username,
                error=str(exc),
            )
            raise QueryError(msg) from exc

    async def list_users(self) -> tuple[User, ...]:
        """List all users ordered by creation date.

        Returns:
            Tuple of all ``User`` records, oldest first.

        Raises:
            QueryError: If the database query or deserialization fails.
        """
        try:
            cursor = await self._db.execute("SELECT * FROM users ORDER BY created_at")
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to list users"
            logger.exception(PERSISTENCE_USER_LIST_FAILED, error=str(exc))
            raise QueryError(msg) from exc
        try:
            users = tuple(_row_to_user(row) for row in rows)
        except (ValueError, ValidationError) as exc:
            msg = "Failed to deserialize users"
            logger.exception(PERSISTENCE_USER_LIST_FAILED, error=str(exc))
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_USER_LISTED, count=len(users))
        return users

    async def count(self) -> int:
        """Return the total number of persisted users.

        Returns:
            Non-negative integer count.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            cursor = await self._db.execute("SELECT COUNT(*) FROM users")
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to count users"
            logger.exception(PERSISTENCE_USER_COUNT_FAILED, error=str(exc))
            raise QueryError(msg) from exc
        result = int(row[0]) if row else 0
        logger.debug(PERSISTENCE_USER_COUNTED, count=result)
        return result

    async def delete(self, user_id: NotBlankStr) -> bool:
        """Delete a user by primary key.

        Args:
            user_id: Unique user identifier.

        Returns:
            ``True`` if a row was deleted, ``False`` if not found.

        Raises:
            QueryError: If the database operation fails.
        """
        try:
            cursor = await self._db.execute(
                "DELETE FROM users WHERE id = ?", (user_id,)
            )
            await self._db.commit()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to delete user {user_id!r}"
            logger.exception(
                PERSISTENCE_USER_DELETE_FAILED,
                user_id=user_id,
                error=str(exc),
            )
            raise QueryError(msg) from exc
        deleted = cursor.rowcount > 0
        logger.info(PERSISTENCE_USER_DELETED, user_id=user_id, deleted=deleted)
        return deleted


class SQLiteApiKeyRepository:
    """SQLite-backed API key repository.

    Provides CRUD operations for ``ApiKey`` models using a shared
    ``aiosqlite.Connection``.  All write operations commit
    immediately.

    Args:
        db: An open aiosqlite connection with ``row_factory``
            set to ``aiosqlite.Row``.
    """

    def __init__(self, db: aiosqlite.Connection) -> None:
        self._db = db

    async def save(self, key: ApiKey) -> None:
        """Persist an API key via upsert (insert or update on conflict).

        Args:
            key: API key model to persist.

        Raises:
            QueryError: If the database operation fails.
        """
        try:
            await self._db.execute(
                """\
INSERT INTO api_keys (id, key_hash, name, role, user_id,
                      created_at, expires_at, revoked)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
    key_hash=excluded.key_hash,
    name=excluded.name,
    role=excluded.role,
    user_id=excluded.user_id,
    expires_at=excluded.expires_at,
    revoked=excluded.revoked""",
                (
                    key.id,
                    key.key_hash,
                    key.name,
                    key.role.value,
                    key.user_id,
                    key.created_at.astimezone(UTC).isoformat(),
                    (
                        key.expires_at.astimezone(UTC).isoformat()
                        if key.expires_at
                        else None
                    ),
                    int(key.revoked),
                ),
            )
            await self._db.commit()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to save API key {key.id!r}"
            logger.exception(
                PERSISTENCE_API_KEY_SAVE_FAILED,
                key_id=key.id,
                error=str(exc),
            )
            raise QueryError(msg) from exc
        logger.info(PERSISTENCE_API_KEY_SAVED, key_id=key.id)

    async def get(self, key_id: NotBlankStr) -> ApiKey | None:
        """Retrieve an API key by primary key.

        Args:
            key_id: Unique key identifier.

        Returns:
            The matching ``ApiKey``, or ``None`` if not found.

        Raises:
            QueryError: If the database query or deserialization fails.
        """
        try:
            cursor = await self._db.execute(
                "SELECT * FROM api_keys WHERE id = ?", (key_id,)
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to fetch API key {key_id!r}"
            logger.exception(
                PERSISTENCE_API_KEY_FETCH_FAILED,
                key_id=key_id,
                error=str(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            logger.debug(PERSISTENCE_API_KEY_FETCHED, key_id=key_id, found=False)
            return None
        try:
            key = _row_to_api_key(row)
        except (ValueError, ValidationError) as exc:
            msg = f"Failed to deserialize API key {key_id!r}"
            logger.exception(
                PERSISTENCE_API_KEY_FETCH_FAILED,
                key_id=key_id,
                error=str(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_API_KEY_FETCHED, key_id=key_id, found=True)
        return key

    async def get_by_hash(self, key_hash: NotBlankStr) -> ApiKey | None:
        """Retrieve an API key by its SHA-256 hash.

        Args:
            key_hash: Hex-encoded SHA-256 digest of the raw key.

        Returns:
            The matching ``ApiKey``, or ``None`` if not found.

        Raises:
            QueryError: If the database query or deserialization fails.
        """
        try:
            cursor = await self._db.execute(
                "SELECT * FROM api_keys WHERE key_hash = ?",
                (key_hash,),
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to fetch API key by hash"
            logger.exception(PERSISTENCE_API_KEY_FETCH_FAILED, error=str(exc))
            raise QueryError(msg) from exc
        if row is None:
            return None
        try:
            return _row_to_api_key(row)
        except (ValueError, ValidationError) as exc:
            msg = "Failed to deserialize API key by hash"
            logger.exception(PERSISTENCE_API_KEY_FETCH_FAILED, error=str(exc))
            raise QueryError(msg) from exc

    async def list_by_user(self, user_id: NotBlankStr) -> tuple[ApiKey, ...]:
        """List all API keys belonging to a user, ordered by creation date.

        Args:
            user_id: Owner user identifier.

        Returns:
            Tuple of ``ApiKey`` records, oldest first.

        Raises:
            QueryError: If the database query or deserialization fails.
        """
        try:
            cursor = await self._db.execute(
                "SELECT * FROM api_keys WHERE user_id = ? ORDER BY created_at",
                (user_id,),
            )
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to list API keys for user {user_id!r}"
            logger.exception(
                PERSISTENCE_API_KEY_LIST_FAILED,
                user_id=user_id,
                error=str(exc),
            )
            raise QueryError(msg) from exc
        try:
            keys = tuple(_row_to_api_key(row) for row in rows)
        except (ValueError, ValidationError) as exc:
            msg = f"Failed to deserialize API keys for user {user_id!r}"
            logger.exception(
                PERSISTENCE_API_KEY_LIST_FAILED,
                user_id=user_id,
                error=str(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(
            PERSISTENCE_API_KEY_LISTED,
            user_id=user_id,
            count=len(keys),
        )
        return keys

    async def delete(self, key_id: NotBlankStr) -> bool:
        """Delete an API key by primary key.

        Args:
            key_id: Unique key identifier.

        Returns:
            ``True`` if a row was deleted, ``False`` if not found.

        Raises:
            QueryError: If the database operation fails.
        """
        try:
            cursor = await self._db.execute(
                "DELETE FROM api_keys WHERE id = ?", (key_id,)
            )
            await self._db.commit()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to delete API key {key_id!r}"
            logger.exception(
                PERSISTENCE_API_KEY_DELETE_FAILED,
                key_id=key_id,
                error=str(exc),
            )
            raise QueryError(msg) from exc
        deleted = cursor.rowcount > 0
        logger.info(PERSISTENCE_API_KEY_DELETED, key_id=key_id, deleted=deleted)
        return deleted
