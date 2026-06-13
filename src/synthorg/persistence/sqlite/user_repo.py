# module-kind: complex_service
"""SQLite repository implementations for User and ApiKey.

Provides ``SQLiteUserRepository`` and ``SQLiteApiKeyRepository``, which
persist ``User`` and ``ApiKey`` domain models to SQLite via aiosqlite.
Both use upsert semantics for ``save`` operations.

One cohesive responsibility: the user-and-api-key persistence family
on SQLite. The two classes share the constraint-classification table
and the row-deserialisation helpers; keeping them in one file matches
the per-family-repo convention used across ``persistence/sqlite/`` and
preserves the SQLite-side parity with the Postgres twin
(``persistence/postgres/user_repo.py``).
"""

import contextlib
import json
import sqlite3

import aiosqlite
from pydantic import ValidationError

from synthorg.api.auth.system_user import is_system_user
from synthorg.core.auth.models import ApiKey, OrgRole, User
from synthorg.core.auth.roles import HumanRole
from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.api_key import (
    PERSISTENCE_API_KEY_COUNT_FAILED,
    PERSISTENCE_API_KEY_DELETE_FAILED,
    PERSISTENCE_API_KEY_FETCH_FAILED,
    PERSISTENCE_API_KEY_FETCHED,
    PERSISTENCE_API_KEY_LIST_FAILED,
    PERSISTENCE_API_KEY_LISTED,
    PERSISTENCE_API_KEY_SAVE_FAILED,
)
from synthorg.observability.events.persistence.user import (
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
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared.datetime_marshaller import (
    coerce_row_timestamp,
    format_iso_utc,
)
from synthorg.persistence._shared.pagination import (
    validate_pagination_args,
)
from synthorg.persistence.constraint_tokens import (
    IDX_SINGLE_CEO,
    LAST_CEO_TRIGGER,
    LAST_OWNER_TRIGGER,
    USERS_USERNAME_UNIQUE,
)
from synthorg.persistence.sqlite._shared import WriteContext
from synthorg.persistence.user_protocol import (
    ApiKeyFilterSpec,
    UserFilterSpec,
)


def _classify_sqlite_user_error(message: str) -> str | None:
    """Map a SQLite error message on the ``users`` table to a stable token.

    SQLite doesn't expose constraint names in its error objects, so
    this function inspects the message once and returns a stable
    identifier.  Callers should match on the return value rather than
    re-parsing the raw error string.

    Returns ``None`` when the message does not match any of the
    known user-table constraints.

    Returns:
        The matching value, or ``None`` when absent.
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

    Raises:
        TypeError: If an argument has the wrong type.
    """
    data = dict(row)
    data["must_change_password"] = bool(data["must_change_password"])
    data["role"] = HumanRole(data["role"])
    data["created_at"] = coerce_row_timestamp(data["created_at"])
    data["updated_at"] = coerce_row_timestamp(data["updated_at"])
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
    data["created_at"] = coerce_row_timestamp(data["created_at"])
    if data["expires_at"] is not None:
        data["expires_at"] = coerce_row_timestamp(data["expires_at"])
    return ApiKey.model_validate(data)


class SQLiteUserRepository:
    """SQLite-backed user repository.

    Provides CRUD operations for ``User`` models using a shared
    ``aiosqlite.Connection``.  All write operations commit
    immediately.

    Args:
        db: An open aiosqlite connection with ``row_factory``
            set to ``aiosqlite.Row``.
        write_context: Async context manager that serializes writes on
            the shared connection. Supplied by
            ``SQLitePersistenceBackend.write_context`` in production;
            tests can pass
            ``tests._shared.persistence.make_private_write_context()``
            for standalone construction.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._write_context = write_context

    async def save(self, user: User) -> None:
        """Persist a user via upsert (insert or update on conflict).

        Args:
            user: User model to persist.

        Raises:
            QueryError: If the database operation fails.
            ConstraintViolationError: If a database constraint is violated.
        """
        async with self._write_context():
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
                        format_iso_utc(user.created_at),
                        format_iso_utc(user.updated_at),
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
            async with self._db.execute(
                "SELECT * FROM users WHERE id = ?", (user_id,)
            ) as cursor:
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
        except (ValueError, TypeError, KeyError, ValidationError) as exc:
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
            async with self._db.execute(
                "SELECT * FROM users WHERE username = ?", (username,)
            ) as cursor:
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
        except (ValueError, TypeError, KeyError, ValidationError) as exc:
            msg = f"Failed to deserialize user {username!r}"
            logger.warning(
                PERSISTENCE_USER_FETCH_FAILED,
                username=username,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[User, ...]:
        """List human users (excludes the system user) with pagination.

        Args:
            limit: Maximum users to return.
            offset: Rows to skip before the window.

        Returns:
            Human users ordered by id ascending.

        Raises:
            QueryError: If the database query or deserialization fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_USER_LIST_FAILED
        )
        try:
            async with self._db.execute(
                "SELECT * FROM users WHERE role != ? ORDER BY id LIMIT ? OFFSET ?",
                (HumanRole.SYSTEM.value, limit, offset),
            ) as cursor:
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
        except (ValueError, TypeError, KeyError, ValidationError) as exc:
            msg = "Failed to deserialize users"
            logger.warning(
                PERSISTENCE_USER_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_USER_LISTED, count=len(users))
        return users

    async def list_after_id(
        self,
        *,
        after_id: NotBlankStr | None = None,
        limit: int = DEFAULT_PAGE_SIZE,
    ) -> tuple[User, ...]:
        """Keyset page of human users with ``id > after_id``.

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(limit, 0, event=PERSISTENCE_USER_LIST_FAILED)
        sql = "SELECT * FROM users WHERE role != ?"
        params: list[object] = [HumanRole.SYSTEM.value]
        if after_id is not None:
            sql += " AND id > ?"
            params.append(after_id)
        sql += " ORDER BY id LIMIT ?"
        params.append(limit)
        try:
            async with self._db.execute(sql, tuple(params)) as cursor:
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
        except (ValueError, TypeError, KeyError, ValidationError) as exc:
            msg = "Failed to deserialize users"
            logger.warning(
                PERSISTENCE_USER_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_USER_LISTED, count=len(users))
        return users

    async def query(
        self,
        filter_spec: UserFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[User, ...]:
        """List users matching the filter spec.

        Args:
            filter_spec: Carries optional filter for role.
            limit: Maximum rows to return.
            offset: Rows to skip before the window.

        Returns:
            Matching users ordered by id ascending.

        Raises:
            QueryError: If the database query or deserialization fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_USER_LIST_FAILED
        )
        sql = "SELECT * FROM users WHERE role != ?"
        params: list[object] = [HumanRole.SYSTEM.value]
        if filter_spec.role is not None:
            sql += " AND role = ?"
            params.append(filter_spec.role.value)
        sql += " ORDER BY id LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        try:
            async with self._db.execute(sql, tuple(params)) as cursor:
                rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to query users"
            logger.warning(
                PERSISTENCE_USER_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        try:
            users = tuple(_row_to_user(row) for row in rows)
        except (ValueError, TypeError, KeyError, ValidationError) as exc:
            msg = "Failed to deserialize users"
            logger.warning(
                PERSISTENCE_USER_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_USER_LISTED, count=len(users))
        return users

    async def count(self, filter_spec: UserFilterSpec) -> int:
        """Count users matching the filter spec.

        Args:
            filter_spec: Carries optional filter for role.

        Returns:
            Total number of matching users.

        Raises:
            QueryError: If the database query fails.
        """
        sql = "SELECT COUNT(*) FROM users WHERE role != ?"
        params: list[object] = [HumanRole.SYSTEM.value]
        if filter_spec.role is not None:
            sql += " AND role = ?"
            params.append(filter_spec.role.value)
        try:
            async with self._db.execute(sql, tuple(params)) as cursor:
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
            async with self._db.execute(
                "SELECT COUNT(*) FROM users WHERE role = ?",
                (role.value,),
            ) as cursor:
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
            ConstraintViolationError: If a database constraint is violated.
        """
        if is_system_user(user_id):
            msg = "System user cannot be deleted"
            logger.warning(
                PERSISTENCE_USER_DELETE_FAILED,
                user_id=user_id,
                error=msg,
            )
            raise QueryError(msg)
        async with self._write_context():
            try:
                async with self._db.execute(
                    "DELETE FROM users WHERE id = ?", (user_id,)
                ) as cursor:
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
        write_context: Async context manager that serializes writes on
            the shared connection. Supplied by
            ``SQLitePersistenceBackend.write_context`` in production;
            tests can pass
            ``tests._shared.persistence.make_private_write_context()``
            for standalone construction.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._write_context = write_context

    async def save(self, key: ApiKey) -> None:
        """Persist an API key via upsert (insert or update on conflict).

        Args:
            key: API key model to persist.

        Raises:
            QueryError: If the database operation fails.
        """
        async with self._write_context():
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
                        format_iso_utc(key.created_at),
                        (format_iso_utc(key.expires_at) if key.expires_at else None),
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
            async with self._db.execute(
                "SELECT * FROM api_keys WHERE id = ?", (key_id,)
            ) as cursor:
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
        except (ValueError, TypeError, KeyError, ValidationError) as exc:
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
            async with self._db.execute(
                "SELECT * FROM api_keys WHERE key_hash = ?",
                (key_hash,),
            ) as cursor:
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
        except (ValueError, TypeError, KeyError, ValidationError) as exc:
            msg = "Failed to deserialize API key by hash"
            logger.warning(
                PERSISTENCE_API_KEY_FETCH_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ApiKey, ...]:
        """List API keys with pagination.

        Args:
            limit: Maximum keys to return.
            offset: Rows to skip before the window.

        Returns:
            API keys ordered by id ascending.

        Raises:
            QueryError: If the database query or deserialization fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_API_KEY_LIST_FAILED
        )
        try:
            async with self._db.execute(
                "SELECT * FROM api_keys ORDER BY id LIMIT ? OFFSET ?",
                (limit, offset),
            ) as cursor:
                rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to list API keys"
            logger.warning(
                PERSISTENCE_API_KEY_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        try:
            keys = tuple(_row_to_api_key(row) for row in rows)
        except (ValueError, TypeError, KeyError, ValidationError) as exc:
            msg = "Failed to deserialize API keys"
            logger.warning(
                PERSISTENCE_API_KEY_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_API_KEY_LISTED, count=len(keys))
        return keys

    async def query(
        self,
        filter_spec: ApiKeyFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ApiKey, ...]:
        """List API keys matching the filter spec.

        Args:
            filter_spec: Carries optional filters for user_id and revoked_only.
            limit: Maximum rows to return.
            offset: Rows to skip before the window.

        Returns:
            Matching API keys ordered by id ascending.

        Raises:
            QueryError: If the database query or deserialization fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_API_KEY_LIST_FAILED
        )
        sql = "SELECT * FROM api_keys WHERE 1=1"
        params: list[object] = []
        if filter_spec.user_id is not None:
            sql += " AND user_id = ?"
            params.append(filter_spec.user_id)
        if filter_spec.revoked_only:
            sql += " AND revoked = ?"
            params.append(1)
        sql += " ORDER BY id LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        try:
            async with self._db.execute(sql, tuple(params)) as cursor:
                rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to query API keys"
            logger.warning(
                PERSISTENCE_API_KEY_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        try:
            keys = tuple(_row_to_api_key(row) for row in rows)
        except (ValueError, TypeError, KeyError, ValidationError) as exc:
            msg = "Failed to deserialize API keys"
            logger.warning(
                PERSISTENCE_API_KEY_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_API_KEY_LISTED, count=len(keys))
        return keys

    async def count(self, filter_spec: ApiKeyFilterSpec) -> int:
        """Count API keys matching the filter spec.

        Args:
            filter_spec: Carries optional filters.

        Returns:
            Total number of matching API keys.

        Raises:
            QueryError: If the database query fails.
        """
        sql = "SELECT COUNT(*) FROM api_keys WHERE 1=1"
        params: list[object] = []
        if filter_spec.user_id is not None:
            sql += " AND user_id = ?"
            params.append(filter_spec.user_id)
        if filter_spec.revoked_only:
            sql += " AND revoked = ?"
            params.append(1)
        try:
            async with self._db.execute(sql, tuple(params)) as cursor:
                row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to count API keys"
            logger.warning(
                PERSISTENCE_API_KEY_COUNT_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        result = int(row[0]) if row else 0
        logger.debug(PERSISTENCE_API_KEY_LISTED, count=result)
        return result

    async def delete(self, key_id: NotBlankStr) -> bool:
        """Delete an API key by primary key.

        Args:
            key_id: Unique key identifier.

        Returns:
            ``True`` if a row was deleted, ``False`` if not found.

        Raises:
            QueryError: If the database operation fails.
        """
        async with self._write_context():
            try:
                async with self._db.execute(
                    "DELETE FROM api_keys WHERE id = ?", (key_id,)
                ) as cursor:
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
