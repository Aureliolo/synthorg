"""SQLite repository implementations for User and ApiKey.

Provides ``SQLiteUserRepository`` and ``SQLiteApiKeyRepository``, which
persist ``User`` and ``ApiKey`` domain models to SQLite via aiosqlite.
Both use upsert semantics for ``save`` operations.
"""

import asyncio
import contextlib
import json
import sqlite3
from datetime import UTC, datetime

import aiosqlite
from pydantic import ValidationError

from synthorg.api.auth.system_user import is_system_user
from synthorg.core.auth.models import ApiKey, OrgRole, User
from synthorg.core.auth.roles import HumanRole
from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_API_KEY_DELETE_FAILED,
    PERSISTENCE_API_KEY_FETCH_FAILED,
    PERSISTENCE_API_KEY_FETCHED,
    PERSISTENCE_API_KEY_LIST_FAILED,
    PERSISTENCE_API_KEY_LISTED,
    PERSISTENCE_API_KEY_SAVE_FAILED,
    PERSISTENCE_USER_COUNT_BY_ROLE_FAILED,
    PERSISTENCE_USER_COUNT_FAILED,
    PERSISTENCE_USER_COUNTED,
    PERSISTENCE_USER_COUNTED_BY_ROLE,
    PERSISTENCE_USER_DELETE_FAILED,
    PERSISTENCE_USER_FETCH_FAILED,
    PERSISTENCE_USER_FETCHED,
    PERSISTENCE_USER_LIST_FAILED,
    PERSISTENCE_USER_LISTED,
    PERSISTENCE_USER_SAVE_FAILED,
)
from synthorg.persistence._shared.pagination import (
    DEFAULT_LIST_LIMIT,
    validate_pagination_args,
)
from synthorg.persistence.constraint_tokens import (
    IDX_SINGLE_CEO,
    LAST_CEO_TRIGGER,
    LAST_OWNER_TRIGGER,
    USERS_USERNAME_UNIQUE,
)


def _classify_sqlite_user_error(message: str) -> str | None:
    """Map a SQLite error message on the ``users`` table to a stable token.

    SQLite doesn't expose constraint names in its error objects, so
    this function inspects the message once and returns a stable
    identifier.  Callers should match on the return value rather than
    re-parsing the raw error string.

    Returns ``None`` when the message does not match any of the
    known user-table constraints.
    """
    lower = message.lower()
    if "cannot remove the last ceo" in lower:
        return LAST_CEO_TRIGGER
    if "cannot remove the last owner" in lower:
        return LAST_OWNER_TRIGGER
    if "unique constraint failed: users.username" in lower:
        return USERS_USERNAME_UNIQUE
    if "unique constraint failed: users.role" in lower or "idx_single_ceo" in lower:
        return IDX_SINGLE_CEO
    return None


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
    # Deserialize JSON columns (may be missing in pre-migration rows).
    raw_org = data.get("org_roles")
    parsed_org = json.loads("[]" if raw_org is None else raw_org)
    if not isinstance(parsed_org, list):
        msg = f"org_roles must be a JSON array, got {type(parsed_org).__name__}"
        raise TypeError(msg)
    data["org_roles"] = tuple(OrgRole(r) for r in parsed_org)
    raw_dept = data.get("scoped_departments")
    parsed_dept = json.loads("[]" if raw_dept is None else raw_dept)
    if not isinstance(parsed_dept, list):
        msg = (
            f"scoped_departments must be a JSON array, got {type(parsed_dept).__name__}"
        )
        raise TypeError(msg)
    data["scoped_departments"] = tuple(parsed_dept)
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

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_lock: asyncio.Lock | None = None,
    ) -> None:
        self._db = db
        # Inject the shared backend write lock so writes from this repo
        # serialize with sibling repos that share the same
        # ``aiosqlite.Connection``; fall back to a private lock for
        # standalone test construction.
        self._write_lock = write_lock if write_lock is not None else asyncio.Lock()

    async def save(self, user: User) -> None:
        """Persist a user via upsert (insert or update on conflict).

        Args:
            user: User model to persist.

        Raises:
            QueryError: If the database operation fails.
        """
        async with self._write_lock:
            try:
                await self._db.execute(
                    """\
INSERT INTO users (id, username, password_hash, role,
                   must_change_password, org_roles,
                   scoped_departments, created_at, updated_at)
VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(id) DO UPDATE SET
    username=excluded.username,
    password_hash=excluded.password_hash,
    role=excluded.role,
    must_change_password=excluded.must_change_password,
    org_roles=excluded.org_roles,
    scoped_departments=excluded.scoped_departments,
    updated_at=excluded.updated_at""",
                    (
                        user.id,
                        user.username,
                        user.password_hash,
                        user.role.value,
                        int(user.must_change_password),
                        json.dumps([r.value for r in user.org_roles]),
                        json.dumps(list(user.scoped_departments)),
                        user.created_at.astimezone(UTC).isoformat(),
                        user.updated_at.astimezone(UTC).isoformat(),
                    ),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to save user {user.id!r}"
                logger.warning(
                    PERSISTENCE_USER_SAVE_FAILED,
                    user_id=user.id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                constraint = _classify_sqlite_user_error(str(exc))
                if constraint is not None:
                    raise ConstraintViolationError(
                        msg,
                        constraint=constraint,
                    ) from exc
                raise QueryError(msg) from exc

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
            logger.warning(
                PERSISTENCE_USER_FETCH_FAILED,
                user_id=user_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            logger.debug(PERSISTENCE_USER_FETCHED, user_id=user_id, found=False)
            return None
        try:
            user = _row_to_user(row)
        except (ValueError, TypeError, ValidationError) as exc:
            msg = f"Failed to deserialize user {user_id!r}"
            logger.warning(
                PERSISTENCE_USER_FETCH_FAILED,
                user_id=user_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
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
            logger.warning(
                PERSISTENCE_USER_FETCH_FAILED,
                username=username,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        try:
            return _row_to_user(row)
        except (ValueError, TypeError, ValidationError) as exc:
            msg = f"Failed to deserialize user {username!r}"
            logger.warning(
                PERSISTENCE_USER_FETCH_FAILED,
                username=username,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def list_users(
        self,
        *,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> tuple[User, ...]:
        """List human users ordered by creation date.

        The system user (internal CLI identity) is excluded from the
        result.  Use ``get`` with the system user ID if you need it.
        For cursor-stable pagination across large user bases use
        :meth:`list_users_paginated` instead.

        Args:
            limit: Maximum users to return (default
                :data:`DEFAULT_LIST_LIMIT`).

        Returns:
            Tuple of human ``User`` records, oldest first, capped at
            *limit* rows.

        Raises:
            QueryError: If the database query or deserialization fails,
                or *limit* is non-int / non-positive.
        """
        validate_pagination_args(limit, 0, event=PERSISTENCE_USER_LIST_FAILED)
        try:
            cursor = await self._db.execute(
                "SELECT * FROM users WHERE role != ? ORDER BY created_at, id LIMIT ?",
                (HumanRole.SYSTEM.value, limit),
            )
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to list users"
            logger.warning(
                PERSISTENCE_USER_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        try:
            users = tuple(_row_to_user(row) for row in rows)
        except (ValueError, TypeError, ValidationError) as exc:
            msg = "Failed to deserialize users"
            logger.warning(
                PERSISTENCE_USER_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_USER_LISTED, count=len(users))
        return users

    async def list_users_paginated(
        self,
        *,
        after_id: NotBlankStr | None,
        limit: int,
    ) -> tuple[User, ...]:
        """Return a single keyset page of human users sorted by ``id``.

        ``WHERE id > after_id ORDER BY id LIMIT N`` so cursor pages
        stay stable under concurrent writes -- offset-based pagination
        would duplicate or skip rows when items shift in the visible
        window between page fetches.

        Args:
            after_id: ``None`` for the first page; the previous
                page's last ``id`` for follow-up pages.
            limit: Page size (rows to return).

        Raises:
            QueryError: If the database query or deserialization fails.
        """
        try:
            if after_id is None:
                cursor = await self._db.execute(
                    "SELECT * FROM users WHERE role != ? ORDER BY id LIMIT ?",
                    (HumanRole.SYSTEM.value, limit),
                )
            else:
                cursor = await self._db.execute(
                    "SELECT * FROM users WHERE role != ? AND id > ? "
                    "ORDER BY id LIMIT ?",
                    (HumanRole.SYSTEM.value, after_id, limit),
                )
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to list users (paginated)"
            logger.warning(
                PERSISTENCE_USER_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        try:
            users = tuple(_row_to_user(row) for row in rows)
        except (ValueError, TypeError, ValidationError, KeyError) as exc:
            # KeyError covers a missing column name in the row factory
            # output (schema drift between the SQL SELECT and the
            # ``_row_to_user`` decoder). Matches the Postgres impl's
            # except tuple so both backends translate the same set of
            # corruption modes into ``QueryError`` instead of leaking
            # the raw exception to the API.
            msg = "Failed to deserialize users (paginated)"
            logger.warning(
                PERSISTENCE_USER_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_USER_LISTED, count=len(users))
        return users

    async def count(self) -> int:
        """Return the number of human users (excludes system user).

        Returns:
            Non-negative integer count.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            cursor = await self._db.execute(
                "SELECT COUNT(*) FROM users WHERE role != ?",
                (HumanRole.SYSTEM.value,),
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to count users"
            logger.warning(
                PERSISTENCE_USER_COUNT_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        result = int(row[0]) if row else 0
        logger.debug(PERSISTENCE_USER_COUNTED, count=result)
        return result

    async def count_by_role(self, role: HumanRole) -> int:
        """Return the number of users with the given role.

        Args:
            role: The role to filter by.

        Returns:
            Non-negative integer count.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            cursor = await self._db.execute(
                "SELECT COUNT(*) FROM users WHERE role = ?",
                (role.value,),
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to count users by role"
            logger.warning(
                PERSISTENCE_USER_COUNT_BY_ROLE_FAILED,
                role=role.value,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        result = int(row[0]) if row else 0
        logger.debug(
            PERSISTENCE_USER_COUNTED_BY_ROLE,
            role=role.value,
            count=result,
        )
        return result

    async def delete(self, user_id: NotBlankStr) -> bool:
        """Delete a user by primary key.

        The system user cannot be deleted -- attempts are rejected
        with a ``QueryError``.

        Args:
            user_id: Unique user identifier.

        Returns:
            ``True`` if a row was deleted, ``False`` if not found.

        Raises:
            QueryError: If the user is the system user or the
                database operation fails.
        """
        if is_system_user(user_id):
            msg = "System user cannot be deleted"
            logger.warning(
                PERSISTENCE_USER_DELETE_FAILED,
                user_id=user_id,
                error=msg,
            )
            raise QueryError(msg)
        async with self._write_lock:
            try:
                cursor = await self._db.execute(
                    "DELETE FROM users WHERE id = ?", (user_id,)
                )
                await self._db.commit()
                deleted = cursor.rowcount > 0
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                constraint = _classify_sqlite_user_error(str(exc))
                if constraint is not None:
                    msg = f"Failed to delete user {user_id!r}"
                    logger.warning(
                        PERSISTENCE_USER_DELETE_FAILED,
                        user_id=user_id,
                        constraint=constraint,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )
                    raise ConstraintViolationError(
                        msg,
                        constraint=constraint,
                    ) from exc
                msg = f"Failed to delete user {user_id!r}"
                logger.warning(
                    PERSISTENCE_USER_DELETE_FAILED,
                    user_id=user_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
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

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_lock: asyncio.Lock | None = None,
    ) -> None:
        self._db = db
        # Inject the shared backend write lock so writes from this repo
        # serialize with sibling repos that share the same
        # ``aiosqlite.Connection``; fall back to a private lock for
        # standalone test construction.
        self._write_lock = write_lock if write_lock is not None else asyncio.Lock()

    async def save(self, key: ApiKey) -> None:
        """Persist an API key via upsert (insert or update on conflict).

        Args:
            key: API key model to persist.

        Raises:
            QueryError: If the database operation fails.
        """
        async with self._write_lock:
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
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to save API key {key.id!r}"
                logger.warning(
                    PERSISTENCE_API_KEY_SAVE_FAILED,
                    key_id=key.id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

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
            logger.warning(
                PERSISTENCE_API_KEY_FETCH_FAILED,
                key_id=key_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            logger.debug(PERSISTENCE_API_KEY_FETCHED, key_id=key_id, found=False)
            return None
        try:
            key = _row_to_api_key(row)
        except (ValueError, TypeError, ValidationError) as exc:
            msg = f"Failed to deserialize API key {key_id!r}"
            logger.warning(
                PERSISTENCE_API_KEY_FETCH_FAILED,
                key_id=key_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_API_KEY_FETCHED, key_id=key_id, found=True)
        return key

    async def get_by_hash(self, key_hash: NotBlankStr) -> ApiKey | None:
        """Retrieve an API key by its HMAC-SHA256 hash.

        Args:
            key_hash: Hex-encoded HMAC-SHA256 digest of the raw key.

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
            logger.warning(
                PERSISTENCE_API_KEY_FETCH_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        try:
            return _row_to_api_key(row)
        except (ValueError, TypeError, ValidationError) as exc:
            msg = "Failed to deserialize API key by hash"
            logger.warning(
                PERSISTENCE_API_KEY_FETCH_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def list_by_user(
        self,
        user_id: NotBlankStr,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[ApiKey, ...]:
        """List up to ``limit`` API keys for a user, ordered by creation date.

        Defaults to a 100-key page; callers needing more must paginate
        with ``offset``.

        Args:
            user_id: Owner user identifier.
            limit: Maximum keys to return (must be >= 1).
            offset: Keys to skip before applying *limit* (must be >= 0).

        Returns:
            Tuple of ``ApiKey`` records, oldest first.

        Raises:
            QueryError: If the database query or deserialization fails.
        """
        validate_pagination_args(
            limit,
            offset,
            event=PERSISTENCE_API_KEY_LIST_FAILED,
            user_id=user_id,
        )
        sql = "SELECT * FROM api_keys WHERE user_id = ? ORDER BY created_at, id"
        params: tuple[object, ...] = (user_id,)
        sql += " LIMIT ? OFFSET ?"
        params = (*params, limit, offset)
        try:
            cursor = await self._db.execute(sql, params)
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to list API keys for user {user_id!r}"
            logger.warning(
                PERSISTENCE_API_KEY_LIST_FAILED,
                user_id=user_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        try:
            keys = tuple(_row_to_api_key(row) for row in rows)
        except (ValueError, TypeError, ValidationError) as exc:
            msg = f"Failed to deserialize API keys for user {user_id!r}"
            logger.warning(
                PERSISTENCE_API_KEY_LIST_FAILED,
                user_id=user_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
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
        async with self._write_lock:
            try:
                cursor = await self._db.execute(
                    "DELETE FROM api_keys WHERE id = ?", (key_id,)
                )
                await self._db.commit()
                deleted = cursor.rowcount > 0
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to delete API key {key_id!r}"
                logger.warning(
                    PERSISTENCE_API_KEY_DELETE_FAILED,
                    key_id=key_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return deleted
