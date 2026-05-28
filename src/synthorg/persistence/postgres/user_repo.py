# module-kind: complex_service
"""Postgres repository implementations for User and ApiKey.

Postgres-native port of ``synthorg.persistence.sqlite.user_repo``.
Uses native BOOLEAN for ``must_change_password`` and ``revoked``,
native TIMESTAMPTZ for ``created_at`` / ``updated_at`` / ``expires_at``,
and native JSONB for ``org_roles`` and ``scoped_departments``.  The
protocol surface returns the same Pydantic models as the SQLite
backend.

One cohesive responsibility: the user-and-api-key persistence family
on Postgres. Mirrors the SQLite sibling structure so the
dual-backend conformance tests in ``tests/conformance/persistence/``
exercise identical surfaces; the two classes share the
constraint-classification table and the dict_row deserialisation
helpers.
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
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_API_KEY_COUNT_FAILED,
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
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared.pagination import (
    validate_pagination_args,
)
from synthorg.persistence.constraint_tokens import (
    IDX_SINGLE_CEO,
    LAST_CEO_TRIGGER,
    LAST_OWNER_TRIGGER,
    USERS_USERNAME_UNIQUE,
)
from synthorg.persistence.user_protocol import (
    ApiKeyFilterSpec,
    UserFilterSpec,
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

    Returns:
        The matching value, or ``None`` when absent.
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

    Returns:
        Result of type ``User``.
    """
    data = dict(row)
    data["role"] = HumanRole(data["role"])
    # org_roles / scoped_departments come back as Python lists.
    data["org_roles"] = tuple(OrgRole(r) for r in (data.get("org_roles") or []))
    data["scoped_departments"] = tuple(data.get("scoped_departments") or [])
    return User.model_validate(data)


def _row_to_api_key(row: dict[str, Any]) -> ApiKey:
    """Reconstruct an ``ApiKey`` from a Postgres dict_row.

    Returns:
        Result of type ``ApiKey``.
    """
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
        """Persist a user via upsert.

        Raises:
            QueryError: If the database query fails.
            ConstraintViolationError: If a database constraint is violated.
        """
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
        """Retrieve a user by primary key.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
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
        """Retrieve a user by unique username.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
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

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[User, ...]:
        """List human users (excludes system user) with pagination.

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
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT * FROM users WHERE role != %s "
                    "ORDER BY id LIMIT %s OFFSET %s",
                    (HumanRole.SYSTEM.value, limit, offset),
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
        sql = "SELECT * FROM users WHERE role != %s"
        params: list[object] = [HumanRole.SYSTEM.value]
        if after_id is not None:
            sql += " AND id > %s"
            params.append(after_id)
        sql += " ORDER BY id LIMIT %s"
        params.append(limit)
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, tuple(params))
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
        sql = "SELECT * FROM users WHERE role != %s"
        params: list[object] = [HumanRole.SYSTEM.value]
        if filter_spec.role is not None:
            sql += " AND role = %s"
            params.append(filter_spec.role.value)
        sql += " ORDER BY id LIMIT %s OFFSET %s"
        params.extend([limit, offset])
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, tuple(params))
                rows = await cur.fetchall()
        except psycopg.Error as exc:
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
        sql = "SELECT COUNT(*) FROM users WHERE role != %s"
        params: list[object] = [HumanRole.SYSTEM.value]
        if filter_spec.role is not None:
            sql += " AND role = %s"
            params.append(filter_spec.role.value)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, tuple(params))
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
        """Return the number of users with the given role.

        Returns:
            Number of matching rows.

        Raises:
            QueryError: If the database query fails.
        """
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
        """Delete a user by primary key. The system user cannot be deleted.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
            ConstraintViolationError: If a database constraint is violated.
        """
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
        """Persist an API key via upsert.

        Raises:
            QueryError: If the database query fails.
        """
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
        """Retrieve an API key by primary key.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
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
        """Retrieve an API key by its HMAC-SHA256 hash.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
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
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT * FROM api_keys ORDER BY id LIMIT %s OFFSET %s",
                    (limit, offset),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
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
        sql = "SELECT * FROM api_keys WHERE TRUE"
        params: list[object] = []
        if filter_spec.user_id is not None:
            sql += " AND user_id = %s"
            params.append(filter_spec.user_id)
        if filter_spec.revoked_only:
            sql += " AND revoked = TRUE"
        sql += " ORDER BY id LIMIT %s OFFSET %s"
        params.extend([limit, offset])
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, tuple(params))
                rows = await cur.fetchall()
        except psycopg.Error as exc:
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
        sql = "SELECT COUNT(*) FROM api_keys WHERE TRUE"
        params: list[object] = []
        if filter_spec.user_id is not None:
            sql += " AND user_id = %s"
            params.append(filter_spec.user_id)
        if filter_spec.revoked_only:
            sql += " AND revoked = TRUE"
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, tuple(params))
                row = await cur.fetchone()
        except psycopg.Error as exc:
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

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
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
