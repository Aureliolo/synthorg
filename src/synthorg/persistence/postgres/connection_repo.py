"""Postgres-backed connection catalog repository.

Persists :class:`Connection` rows in the ``connections`` table using
the shared ``AsyncConnectionPool``.  Read paths use
``psycopg.rows.dict_row`` so column access is by name.

Mutation audit logging stays at the service layer
(``ConnectionService``); this repo only emits fetch / error events
per the persistence-boundary rule.
"""

from typing import TYPE_CHECKING, Any

from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from synthorg.core.persistence_errors import QueryError
from synthorg.core.resilience_config import RateLimiterConfig
from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.models import (
    AuthMethod,
    Connection,
    ConnectionStatus,
    ConnectionType,
    SecretRef,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_CONNECTION_DELETE_FAILED,
    PERSISTENCE_CONNECTION_DESERIALIZE_FAILED,
    PERSISTENCE_CONNECTION_FETCH_FAILED,
    PERSISTENCE_CONNECTION_LIST_FAILED,
    PERSISTENCE_CONNECTION_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import (
    coerce_row_timestamp,
    normalize_utc,
    safe_int,
    validate_pagination_args,
)
from synthorg.persistence.connection_protocol import ConnectionFilterSpec

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool


logger = get_logger(__name__)


_SELECT_COLS = (
    "name, connection_type, auth_method, base_url, secret_refs_json, "
    "rate_limit_rpm, rate_limit_concurrent, health_check_enabled, "
    "health_status, last_health_check_at, metadata_json, "
    "webhook_receipt_retention_days, sensitive, created_at, updated_at"
)


def _row_to_connection(row: dict[str, Any]) -> Connection:
    """Deserialize a psycopg dict row into a :class:`Connection`.

    Returns:
        Result of type ``Connection``.
    """
    secret_refs_payload = row.get("secret_refs_json") or []
    secret_refs = tuple(SecretRef(**entry) for entry in secret_refs_payload)
    metadata = row.get("metadata_json") or {}
    rate_limit_rpm = safe_int(row["rate_limit_rpm"], default=0)
    rate_limit_concurrent = safe_int(row["rate_limit_concurrent"], default=0)
    rate_limiter = (
        RateLimiterConfig(
            max_requests_per_minute=rate_limit_rpm,
            max_concurrent=rate_limit_concurrent,
        )
        if rate_limit_rpm or rate_limit_concurrent
        else None
    )
    last_health_check_at = row.get("last_health_check_at")
    retention = row.get("webhook_receipt_retention_days")
    sensitive = bool(row.get("sensitive", False))
    return Connection(
        name=NotBlankStr(row["name"]),
        connection_type=ConnectionType(row["connection_type"]),
        auth_method=AuthMethod(row["auth_method"]),
        base_url=NotBlankStr(row["base_url"]) if row["base_url"] else None,
        secret_refs=secret_refs,
        rate_limiter=rate_limiter,
        health_check_enabled=bool(row["health_check_enabled"]),
        health_status=ConnectionStatus(row["health_status"]),
        last_health_check_at=(
            coerce_row_timestamp(last_health_check_at) if last_health_check_at else None
        ),
        metadata=metadata,
        webhook_receipt_retention_days=safe_int(retention, default=None),
        sensitive=sensitive,
        created_at=coerce_row_timestamp(row["created_at"]),
        updated_at=coerce_row_timestamp(row["updated_at"]),
    )


class PostgresConnectionRepository:
    """Postgres implementation of :class:`ConnectionRepository`."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        """Bind to the shared *pool*."""
        self._pool = pool

    async def save(self, connection: Connection) -> None:
        """Upsert a connection row keyed by ``name``.

        Raises:
            QueryError: If the database query fails.
        """
        secret_refs_payload = [
            ref.model_dump(mode="json") for ref in connection.secret_refs
        ]
        rate_limit_rpm = (
            connection.rate_limiter.max_requests_per_minute
            if connection.rate_limiter is not None
            else 0
        )
        rate_limit_concurrent = (
            connection.rate_limiter.max_concurrent
            if connection.rate_limiter is not None
            else 0
        )
        params = (
            str(connection.name),
            connection.connection_type.value,
            connection.auth_method.value,
            str(connection.base_url) if connection.base_url else None,
            Jsonb(secret_refs_payload),
            rate_limit_rpm,
            rate_limit_concurrent,
            connection.health_check_enabled,
            connection.health_status.value,
            (
                normalize_utc(connection.last_health_check_at)
                if connection.last_health_check_at is not None
                else None
            ),
            Jsonb(connection.metadata),
            connection.webhook_receipt_retention_days,
            connection.sensitive,
            normalize_utc(connection.created_at),
            normalize_utc(connection.updated_at),
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO connections (
                        name, connection_type, auth_method, base_url,
                        secret_refs_json, rate_limit_rpm, rate_limit_concurrent,
                        health_check_enabled, health_status,
                        last_health_check_at, metadata_json,
                        webhook_receipt_retention_days, sensitive,
                        created_at, updated_at
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    )
                    ON CONFLICT (name) DO UPDATE SET
                        connection_type = EXCLUDED.connection_type,
                        auth_method = EXCLUDED.auth_method,
                        base_url = EXCLUDED.base_url,
                        secret_refs_json = EXCLUDED.secret_refs_json,
                        rate_limit_rpm = EXCLUDED.rate_limit_rpm,
                        rate_limit_concurrent = EXCLUDED.rate_limit_concurrent,
                        health_check_enabled = EXCLUDED.health_check_enabled,
                        health_status = EXCLUDED.health_status,
                        last_health_check_at = EXCLUDED.last_health_check_at,
                        metadata_json = EXCLUDED.metadata_json,
                        webhook_receipt_retention_days =
                            EXCLUDED.webhook_receipt_retention_days,
                        sensitive = EXCLUDED.sensitive,
                        updated_at = EXCLUDED.updated_at
                    """,
                    params,
                )
        except Exception as exc:
            msg = f"Failed to save connection {connection.name!r}"
            logger.warning(
                PERSISTENCE_CONNECTION_SAVE_FAILED,
                name=str(connection.name),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def get(self, name: NotBlankStr) -> Connection | None:
        """Fetch a single connection by name.

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
                    f"SELECT {_SELECT_COLS} FROM connections "  # noqa: S608
                    "WHERE name = %s",
                    (str(name),),
                )
                row = await cur.fetchone()
        except Exception as exc:
            msg = f"Failed to fetch connection {name!r}"
            logger.warning(
                PERSISTENCE_CONNECTION_FETCH_FAILED,
                name=str(name),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        try:
            return _row_to_connection(row)
        except (ValueError, TypeError) as exc:
            msg = f"Failed to deserialize connection {name!r}"
            logger.warning(
                PERSISTENCE_CONNECTION_DESERIALIZE_FAILED,
                name=str(name),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Connection, ...]:
        """List all connections, sorted by name for determinism.

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_CONNECTION_LIST_FAILED
        )
        sql = (
            f"SELECT {_SELECT_COLS} FROM connections "  # noqa: S608
            "ORDER BY name ASC LIMIT %s OFFSET %s"
        )
        params: tuple[object, ...] = (limit, offset)
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, params)
                rows = await cur.fetchall()
        except Exception as exc:
            msg = "Failed to list connections"
            logger.warning(
                PERSISTENCE_CONNECTION_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        try:
            return tuple(_row_to_connection(row) for row in rows)
        except (ValueError, TypeError) as exc:
            msg = "Failed to deserialize connection rows"
            logger.warning(
                PERSISTENCE_CONNECTION_DESERIALIZE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def query(
        self,
        filter_spec: ConnectionFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Connection, ...]:
        """List connections matching the filter spec, sorted by name.

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_CONNECTION_LIST_FAILED
        )
        sql = f"SELECT {_SELECT_COLS} FROM connections"  # noqa: S608
        params: tuple[object, ...] = ()
        if filter_spec.connection_type is not None:
            sql += " WHERE connection_type = %s"
            params = (filter_spec.connection_type.value,)
        sql += " ORDER BY name ASC LIMIT %s OFFSET %s"
        params = (*params, limit, offset)
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, params)
                rows = await cur.fetchall()
        except Exception as exc:
            msg = "Failed to list connections matching filter"
            logger.warning(
                PERSISTENCE_CONNECTION_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        try:
            return tuple(_row_to_connection(row) for row in rows)
        except (ValueError, TypeError) as exc:
            msg = "Failed to deserialize connection rows"
            logger.warning(
                PERSISTENCE_CONNECTION_DESERIALIZE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def count(self, filter_spec: ConnectionFilterSpec) -> int:
        """Count connections matching the filter spec.

        Returns:
            Number of matching rows.

        Raises:
            QueryError: If the database query fails.
        """
        sql = "SELECT COUNT(*) FROM connections"
        params: tuple[object, ...] = ()
        if filter_spec.connection_type is not None:
            sql += " WHERE connection_type = %s"
            params = (filter_spec.connection_type.value,)
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, params)
                row = await cur.fetchone()
                return row["count"] if row else 0
        except Exception as exc:
            msg = "Failed to count connections"
            logger.warning(
                PERSISTENCE_CONNECTION_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def delete(self, name: NotBlankStr) -> bool:
        """Delete a connection by name; return ``True`` if a row was removed.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM connections WHERE name = %s",
                    (str(name),),
                )
                deleted = cur.rowcount > 0
        except Exception as exc:
            msg = f"Failed to delete connection {name!r}"
            logger.warning(
                PERSISTENCE_CONNECTION_DELETE_FAILED,
                name=str(name),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return deleted
