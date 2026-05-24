"""SQLite-backed connection catalog repository.

Persists :class:`Connection` rows in the ``connections`` table.  Bound
to an open :class:`aiosqlite.Connection` at construction; the
persistence backend owns connection lifecycle.

Mutation audit logging stays at the service layer
(``ConnectionService``); this repo only emits fetch / error events
per the persistence-boundary rule documented in
``docs/reference/persistence-boundary.md``.
"""

import contextlib
import json
import sqlite3
from typing import Any

import aiosqlite

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
    format_iso_utc,
    validate_pagination_args,
)
from synthorg.persistence.connection_protocol import ConnectionFilterSpec  # noqa: TC001
from synthorg.persistence.sqlite._shared import WriteContext  # noqa: TC001

logger = get_logger(__name__)


_SELECT_COLS = (
    "name, connection_type, auth_method, base_url, secret_refs_json, "
    "rate_limit_rpm, rate_limit_concurrent, health_check_enabled, "
    "health_status, last_health_check_at, metadata_json, "
    "webhook_receipt_retention_days, sensitive, created_at, updated_at"
)


def _row_to_connection(row: aiosqlite.Row | tuple[Any, ...]) -> Connection:
    """Deserialize a row tuple into a :class:`Connection` model.

    Returns:
        Result of type ``Connection``.
    """
    (
        name,
        connection_type,
        auth_method,
        base_url,
        secret_refs_json,
        rate_limit_rpm,
        rate_limit_concurrent,
        health_check_enabled,
        health_status,
        last_health_check_at,
        metadata_json,
        webhook_receipt_retention_days,
        sensitive,
        created_at,
        updated_at,
    ) = row
    secret_refs_payload = json.loads(secret_refs_json or "[]")
    secret_refs = tuple(SecretRef(**entry) for entry in secret_refs_payload)
    metadata = json.loads(metadata_json or "{}")
    rate_limiter = (
        RateLimiterConfig(
            max_requests_per_minute=int(rate_limit_rpm),
            max_concurrent=int(rate_limit_concurrent),
        )
        if rate_limit_rpm or rate_limit_concurrent
        else None
    )
    return Connection(
        name=NotBlankStr(name),
        connection_type=ConnectionType(connection_type),
        auth_method=AuthMethod(auth_method),
        base_url=NotBlankStr(base_url) if base_url else None,
        secret_refs=secret_refs,
        rate_limiter=rate_limiter,
        health_check_enabled=bool(health_check_enabled),
        health_status=ConnectionStatus(health_status),
        last_health_check_at=(
            coerce_row_timestamp(last_health_check_at) if last_health_check_at else None
        ),
        metadata=metadata,
        webhook_receipt_retention_days=(
            int(webhook_receipt_retention_days)
            if webhook_receipt_retention_days is not None
            else None
        ),
        sensitive=bool(sensitive),
        created_at=coerce_row_timestamp(created_at),
        updated_at=coerce_row_timestamp(updated_at),
    )


class SQLiteConnectionRepository:
    """SQLite implementation of :class:`ConnectionRepository`."""

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        """Bind to *db* and serialize writes via *write_context*."""
        self._db = db
        self._write_context = write_context

    async def save(self, connection: Connection) -> None:
        """Upsert a connection row keyed by ``name``.

        Raises:
            QueryError: If the database query fails.
        """
        secret_refs_json = json.dumps(
            [ref.model_dump(mode="json") for ref in connection.secret_refs],
        )
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
        metadata_json = json.dumps(connection.metadata)
        created_at_iso = format_iso_utc(connection.created_at)
        updated_at_iso = format_iso_utc(connection.updated_at)
        last_health_check_at_iso = (
            format_iso_utc(connection.last_health_check_at)
            if connection.last_health_check_at is not None
            else None
        )
        async with self._write_context():
            try:
                await self._db.execute(
                    """
                    INSERT INTO connections (
                        name, connection_type, auth_method, base_url,
                        secret_refs_json, rate_limit_rpm, rate_limit_concurrent,
                        health_check_enabled, health_status,
                        last_health_check_at, metadata_json,
                        webhook_receipt_retention_days, sensitive,
                        created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(name) DO UPDATE SET
                        connection_type = excluded.connection_type,
                        auth_method = excluded.auth_method,
                        base_url = excluded.base_url,
                        secret_refs_json = excluded.secret_refs_json,
                        rate_limit_rpm = excluded.rate_limit_rpm,
                        rate_limit_concurrent = excluded.rate_limit_concurrent,
                        health_check_enabled = excluded.health_check_enabled,
                        health_status = excluded.health_status,
                        last_health_check_at = excluded.last_health_check_at,
                        metadata_json = excluded.metadata_json,
                        webhook_receipt_retention_days =
                            excluded.webhook_receipt_retention_days,
                        sensitive = excluded.sensitive,
                        updated_at = excluded.updated_at
                    """,
                    (
                        str(connection.name),
                        connection.connection_type.value,
                        connection.auth_method.value,
                        str(connection.base_url) if connection.base_url else None,
                        secret_refs_json,
                        rate_limit_rpm,
                        rate_limit_concurrent,
                        1 if connection.health_check_enabled else 0,
                        connection.health_status.value,
                        last_health_check_at_iso,
                        metadata_json,
                        connection.webhook_receipt_retention_days,
                        1 if connection.sensitive else 0,
                        created_at_iso,
                        updated_at_iso,
                    ),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
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
            async with self._db.execute(
                f"SELECT {_SELECT_COLS} FROM connections WHERE name = ?",  # noqa: S608
                (str(name),),
            ) as cursor:
                row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
            "ORDER BY name ASC LIMIT ? OFFSET ?"
        )
        params: tuple[object, ...] = (limit, offset)
        try:
            async with self._db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
            Tuple of (items, next_cursor) for paginated iteration.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_CONNECTION_LIST_FAILED
        )
        sql = f"SELECT {_SELECT_COLS} FROM connections"  # noqa: S608
        params: tuple[object, ...] = ()
        if filter_spec.connection_type is not None:
            sql += " WHERE connection_type = ?"
            params = (filter_spec.connection_type.value,)
        sql += " ORDER BY name ASC LIMIT ? OFFSET ?"
        params = (*params, limit, offset)
        try:
            async with self._db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
            sql += " WHERE connection_type = ?"
            params = (filter_spec.connection_type.value,)
        try:
            async with self._db.execute(sql, params) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else 0
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "DELETE FROM connections WHERE name = ?",
                    (str(name),),
                )
                deleted = cursor.rowcount > 0
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to delete connection {name!r}"
                logger.warning(
                    PERSISTENCE_CONNECTION_DELETE_FAILED,
                    name=str(name),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return deleted
