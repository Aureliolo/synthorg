"""Postgres repository implementations for User and ApiKey.

Postgres-native port of ``synthorg.persistence.sqlite.user_repo``.
Uses native BOOLEAN for ``must_change_password`` and ``revoked``,
native TIMESTAMPTZ for ``created_at`` / ``updated_at`` / ``expires_at``,
and native JSONB for ``org_roles`` and ``scoped_departments``.  The
protocol surface returns the same Pydantic models as the SQLite
backend.
"""

from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
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

_PG_CONSTRAINT_MAP: dict[str, str] = {
    "idx_single_ceo": IDX_SINGLE_CEO,
    "users_username_key": USERS_USERNAME_UNIQUE,
}

_PG_MESSAGE_MAP: tuple[tuple[str, str], ...] = (
    ("cannot remove the last ceo", LAST_CEO_TRIGGER),
    ("cannot remove the last owner", LAST_OWNER_TRIGGER),
    ("users_username_key", USERS_USERNAME_UNIQUE),
    ("users.username", USERS_USERNAME_UNIQUE),
    ("idx_single_ceo", IDX_SINGLE_CEO),
)


def _classify_postgres_user_error(exc: psycopg.Error) -> str | None:
    """Map a psycopg error on the ``users`` table to a stable token.

    Postgres exposes the constraint name via ``exc.diag.constraint_name``
    for unique/foreign-key violations.  For trigger-raised exceptions
    the constraint name is usually empty, so we fall back to matching
    the error message against our known trigger messages.
    """
    constraint = getattr(getattr(exc, "diag", None), "constraint_name", "") or ""
    if constraint in _PG_CONSTRAINT_MAP:
        return _PG_CONSTRAINT_MAP[constraint]
    message = str(exc).lower()
    for token, classified in _PG_MESSAGE_MAP:
        if token in message:
            return classified
    return None


if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = get_logger(__name__)


def _row_to_user(row: dict[str, Any]) -> User:
    """Reconstruct a ``User`` from a Postgres dict_row.

    Postgres returns JSONB as Python list/dict (no json.loads needed),
    TIMESTAMPTZ as timezone-aware datetime (no fromisoformat needed),
    and BOOLEAN as bool.  The only work left is enum construction.
    """
    data = dict(row)
    data["role"] = HumanRole(data["role"])
    # org_roles / scoped_departments come back as Python lists.
    data["org_roles"] = tuple(OrgRole(r) for r in (data.get("org_roles") or []))
    data["scoped_departments"] = tuple(data.get("scoped_departments") or [])
    return User.model_validate(data)


def _row_to_api_key(row: dict[str, Any]) -> ApiKey:
    """Reconstruct an ``ApiKey`` from a Postgres dict_row."""
    data = dict(row)
    data["role"] = HumanRole(data["role"])
    return ApiKey.model_validate(data)


class PostgresUserRepository:
    """Postgres-backed user repository.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, user: User) -> None:
        """Persist a user via upsert."""
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO users (id, username, password_hash, role,
                                       must_change_password, org_roles,
                                       scoped_departments, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT(id) DO UPDATE SET
                        username=EXCLUDED.username,
                        password_hash=EXCLUDED.password_hash,
                        role=EXCLUDED.role,
                        must_change_password=EXCLUDED.must_change_password,
                        org_roles=EXCLUDED.org_roles,
                        scoped_departments=EXCLUDED.scoped_departments,
                        updated_at=EXCLUDED.updated_at
                    """,
                    (
                        user.id,
                        user.username,
                        user.password_hash,
                        user.role.value,
                        user.must_change_password,
                        Jsonb([r.value for r in user.org_roles]),
                        Jsonb(list(user.scoped_departments)),
                        user.created_at,
                        user.updated_at,
                    ),
                )
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to save user {user.id!r}"
            logger.warning(
                PERSISTENCE_USER_SAVE_FAILED,
                user_id=user.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            constraint = _classify_postgres_user_error(exc)
            if constraint is not None:
                raise ConstraintViolationError(
                    msg,
                    constraint=constraint,
                ) from exc
            raise QueryError(msg) from exc

    async def get(self, user_id: NotBlankStr) -> User | None:
        """Retrieve a user by primary key."""
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute("SELECT * FROM users WHERE id = %s", (user_id,))
                row = await cur.fetchone()
        except psycopg.Error as exc:
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
        """Retrieve a user by unique username."""
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT * FROM users WHERE username = %s", (username,)
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
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

    async def list_users(
        self,
        *,
        limit: int = DEFAULT_LIST_LIMIT,
    ) -> tuple[User, ...]:
        """List human users ordered by creation date (excludes system user).

        Bounded by *limit* so an unauth'd caller cannot materialise an
        unbounded tuple of users. For cursor-stable pagination across
        large user bases use :meth:`list_users_paginated` instead.

        Args:
            limit: Maximum users to return (default
                :data:`DEFAULT_LIST_LIMIT`).

        Raises:
            QueryError: If the database query, deserialization, or
                pagination validation fails.
        """
        limit = validate_pagination_args(limit, 0, event=PERSISTENCE_USER_LIST_FAILED)
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT * FROM users WHERE role != %s "
                    "ORDER BY created_at, id LIMIT %s",
                    (HumanRole.SYSTEM.value, limit),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
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

    async def list_users_paginated(
        self,
        *,
        after_id: NotBlankStr | None,
        limit: int,
    ) -> tuple[User, ...]:
        """Return a single keyset page of human users sorted by ``id``."""
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                if after_id is None:
                    await cur.execute(
                        "SELECT * FROM users WHERE role != %s ORDER BY id LIMIT %s",
                        (HumanRole.SYSTEM.value, limit),
                    )
                else:
                    await cur.execute(
                        "SELECT * FROM users WHERE role != %s AND id > %s "
                        "ORDER BY id LIMIT %s",
                        (HumanRole.SYSTEM.value, after_id, limit),
                    )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to list users (paginated)"
            logger.warning(
                PERSISTENCE_USER_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        try:
            users = tuple(_row_to_user(row) for row in rows)
        except (ValueError, TypeError, KeyError, ValidationError) as exc:
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
        """Return the number of human users (excludes system user)."""
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "SELECT COUNT(*) FROM users WHERE role != %s",
                    (HumanRole.SYSTEM.value,),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
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
        """Return the number of users with the given role."""
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "SELECT COUNT(*) FROM users WHERE role = %s",
                    (role.value,),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = "Failed to count users by role"
            logger.warning(
                PERSISTENCE_USER_COUNT_BY_ROLE_FAILED,
                role=role.value,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        result = int(row[0]) if row else 0
        logger.debug(PERSISTENCE_USER_COUNTED_BY_ROLE, role=role.value, count=result)
        return result

    async def delete(self, user_id: NotBlankStr) -> bool:
        """Delete a user by primary key. The system user cannot be deleted."""
        if is_system_user(user_id):
            msg = "System user cannot be deleted"
            logger.warning(PERSISTENCE_USER_DELETE_FAILED, user_id=user_id, error=msg)
            raise QueryError(msg)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute("DELETE FROM users WHERE id = %s", (user_id,))
                deleted = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            constraint = _classify_postgres_user_error(exc)
            if constraint:
                msg = f"Failed to delete user {user_id!r}"
                logger.warning(
                    PERSISTENCE_USER_DELETE_FAILED,
                    user_id=user_id,
                    constraint=constraint,
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


class PostgresApiKeyRepository:
    """Postgres-backed API key repository.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, key: ApiKey) -> None:
        """Persist an API key via upsert."""
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO api_keys (id, key_hash, name, role, user_id,
                                          created_at, expires_at, revoked)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT(id) DO UPDATE SET
                        key_hash=EXCLUDED.key_hash,
                        name=EXCLUDED.name,
                        role=EXCLUDED.role,
                        user_id=EXCLUDED.user_id,
                        expires_at=EXCLUDED.expires_at,
                        revoked=EXCLUDED.revoked
                    """,
                    (
                        key.id,
                        key.key_hash,
                        key.name,
                        key.role.value,
                        key.user_id,
                        key.created_at,
                        key.expires_at,
                        key.revoked,
                    ),
                )
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to save API key {key.id!r}"
            logger.warning(
                PERSISTENCE_API_KEY_SAVE_FAILED,
                key_id=key.id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def get(self, key_id: NotBlankStr) -> ApiKey | None:
        """Retrieve an API key by primary key."""
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute("SELECT * FROM api_keys WHERE id = %s", (key_id,))
                row = await cur.fetchone()
        except psycopg.Error as exc:
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
        """Retrieve an API key by its HMAC-SHA256 hash."""
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT * FROM api_keys WHERE key_hash = %s", (key_hash,)
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
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

    async def list_by_user(
        self,
        user_id: NotBlankStr,
        *,
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> tuple[ApiKey, ...]:
        """List up to ``limit`` API keys for a user, ordered by creation date.

        Defaults to :data:`DEFAULT_LIST_LIMIT`; callers needing more
        must paginate with ``offset``.
        """
        validate_pagination_args(
            limit,
            offset,
            event=PERSISTENCE_API_KEY_LIST_FAILED,
            user_id=user_id,
        )
        sql = "SELECT * FROM api_keys WHERE user_id = %s ORDER BY created_at, id"
        params: tuple[object, ...] = (user_id,)
        sql += " LIMIT %s OFFSET %s"
        params = (*params, limit, offset)
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, params)
                rows = await cur.fetchall()
        except psycopg.Error as exc:
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
        except (ValueError, TypeError, KeyError, ValidationError) as exc:
            msg = f"Failed to deserialize API keys for user {user_id!r}"
            logger.warning(
                PERSISTENCE_API_KEY_LIST_FAILED,
                user_id=user_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_API_KEY_LISTED, user_id=user_id, count=len(keys))
        return keys

    async def delete(self, key_id: NotBlankStr) -> bool:
        """Delete an API key by primary key."""
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute("DELETE FROM api_keys WHERE id = %s", (key_id,))
                deleted = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to delete API key {key_id!r}"
            logger.warning(
                PERSISTENCE_API_KEY_DELETE_FAILED,
                key_id=key_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return deleted
