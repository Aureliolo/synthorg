# module-kind: repository
"""Postgres implementation of the SettingsRepository protocol.

Postgres stores ``updated_at`` as a native ``TIMESTAMPTZ`` column
(SQLite stores ISO 8601 strings).  The repository converts to and
from ISO strings at the boundary so the protocol surface
(SettingRow instances) is identical for both backends. The
optimistic-concurrency write paths live in
:mod:`synthorg.persistence.postgres._settings_cas`.
"""

from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, cast

import psycopg
from psycopg.rows import dict_row
from pydantic import ValidationError

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import (
    SETTINGS_DELETE_FAILED,
    SETTINGS_FETCH_FAILED,
    SETTINGS_SET_FAILED,
    SETTINGS_VALUE_SET,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import format_iso_utc, validate_pagination_args
from synthorg.persistence.postgres._settings_cas import (
    parse_setting_iso,
)
from synthorg.persistence.postgres._settings_cas import (
    set_if_unchanged as cas_set_if_unchanged,
)
from synthorg.persistence.postgres._settings_cas import (
    set_many as cas_set_many,
)
from synthorg.persistence.settings_protocol import (
    SettingRow,
    SettingRowKey,
)

if TYPE_CHECKING:
    from datetime import datetime

    from psycopg_pool import AsyncConnectionPool

logger = get_logger(__name__)


class PostgresSettingsRepository:
    """Postgres-backed namespaced settings repository.

    Settings are stored in the ``settings`` table with a composite
    primary key of ``(namespace, key)``.  The ``updated_at`` column is
    ``TIMESTAMPTZ`` in Postgres; the protocol surface speaks ISO 8601
    strings and this repository handles the conversion.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, entity: SettingRow) -> None:
        """Persist a setting (upsert by composite key).

        Raises:
            QueryError: If the database query fails.
        """
        updated_at_dt = parse_setting_iso(
            entity.updated_at, entity.namespace, entity.key
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "INSERT INTO settings "
                    "(namespace, key, value, updated_at) "
                    "VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT (namespace, key) DO UPDATE SET "
                    "value = EXCLUDED.value, "
                    "updated_at = EXCLUDED.updated_at",
                    (entity.namespace, entity.key, entity.value, updated_at_dt),
                )
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to save setting {entity.namespace}/{entity.key}"
            logger.warning(
                SETTINGS_SET_FAILED,
                namespace=entity.namespace,
                key=entity.key,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(
            SETTINGS_VALUE_SET,
            namespace=entity.namespace,
            key=entity.key,
        )

    async def get(
        self,
        entity_id: SettingRowKey,
    ) -> SettingRow | None:
        """Retrieve a setting by composite key.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        namespace, key = entity_id
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT namespace, key, value, updated_at FROM settings "
                    "WHERE namespace = %s AND key = %s",
                    (namespace, key),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Failed to get setting {namespace}/{key}"
            logger.warning(
                SETTINGS_FETCH_FAILED,
                namespace=namespace,
                key=key,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        try:
            return SettingRow(
                namespace=row["namespace"],
                key=row["key"],
                value=row["value"],
                updated_at=format_iso_utc(cast("datetime", row["updated_at"])),
            )
        except (ValidationError, ValueError) as exc:
            msg = f"Failed to deserialize setting row {namespace}/{key}"
            logger.warning(
                SETTINGS_FETCH_FAILED,
                namespace=namespace,
                key=key,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                note="deserialization failed",
            )
            raise QueryError(msg) from exc

    async def get_namespace(
        self,
        namespace: NotBlankStr,
    ) -> tuple[SettingRow, ...]:
        """Retrieve all settings in a namespace.

        Returns:
            Tuple of matching rows; empty when no rows match.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT namespace, key, value, updated_at FROM settings "
                    "WHERE namespace = %s ORDER BY key",
                    (namespace,),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = f"Failed to get settings for namespace {namespace}"
            logger.warning(
                SETTINGS_FETCH_FAILED,
                namespace=namespace,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        results: list[SettingRow] = []
        for r in rows:
            try:
                results.append(
                    SettingRow(
                        namespace=r["namespace"],
                        key=r["key"],
                        value=r["value"],
                        updated_at=format_iso_utc(cast("datetime", r["updated_at"])),
                    )
                )
            except (ValidationError, ValueError) as exc:
                msg = f"Failed to deserialize setting row {namespace}/{r['key']}"
                logger.warning(
                    SETTINGS_FETCH_FAILED,
                    namespace=namespace,
                    key=r["key"],
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    note="deserialization failed",
                )
                raise QueryError(msg) from exc
        return tuple(results)

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[SettingRow, ...]:
        """List settings across all namespaces (paginated).

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        # Validate + clamp via the shared helper (rejects limit < 1 /
        # offset < 0, caps at the repo-wide MAX_LIST_LIMIT ceiling) so
        # no inline magic ceiling and no sentinel (-1) slips through.
        effective_limit = validate_pagination_args(
            limit, offset, event=SETTINGS_FETCH_FAILED
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT namespace, key, value, updated_at FROM settings "
                    "ORDER BY namespace, key LIMIT %s OFFSET %s",
                    (effective_limit, offset),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to list settings"
            logger.warning(
                SETTINGS_FETCH_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        results: list[SettingRow] = []
        for r in rows:
            try:
                results.append(
                    SettingRow(
                        namespace=r["namespace"],
                        key=r["key"],
                        value=r["value"],
                        updated_at=format_iso_utc(cast("datetime", r["updated_at"])),
                    )
                )
            except (ValidationError, ValueError) as exc:
                msg = f"Failed to deserialize setting row {r['namespace']}/{r['key']}"
                logger.warning(
                    SETTINGS_FETCH_FAILED,
                    namespace=r["namespace"],
                    key=r["key"],
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    note="deserialization failed",
                )
                raise QueryError(msg) from exc
        return tuple(results)

    async def set_if_unchanged(
        self,
        entity: SettingRow,
        expected_updated_at: str | None = None,
    ) -> bool:
        """Upsert a setting with optional compare-and-swap (bespoke per D7).

        Delegates to
        :func:`synthorg.persistence.postgres._settings_cas.set_if_unchanged`.

        Returns:
            ``True`` if the write succeeded, ``False`` if the CAS
            condition was not met.

        Raises:
            QueryError: If the database query fails.
        """
        return await cas_set_if_unchanged(self._pool, entity, expected_updated_at)

    async def set_many(
        self,
        items: Sequence[SettingRow],
        *,
        expected_updated_at_map: (Mapping[SettingRowKey, str] | None) = None,
    ) -> bool:
        """Atomically upsert multiple settings.

        Delegates to
        :func:`synthorg.persistence.postgres._settings_cas.set_many`.

        Returns:
            True when all rows were upserted, False when a CAS conflict
            caused the transaction to roll back.

        Raises:
            QueryError: If the database query fails.
        """
        return await cas_set_many(
            self._pool, items, expected_updated_at_map=expected_updated_at_map
        )

    async def delete(self, entity_id: SettingRowKey) -> bool:
        """Delete a setting by composite key.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        namespace, key = entity_id
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM settings WHERE namespace = %s AND key = %s",
                    (namespace, key),
                )
                deleted = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to delete setting {namespace}/{key}"
            logger.warning(
                SETTINGS_DELETE_FAILED,
                namespace=namespace,
                key=key,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return deleted

    async def delete_namespace(self, namespace: NotBlankStr) -> int:
        """Delete all settings in a namespace. Return count.

        Returns:
            Number of rows deleted.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM settings WHERE namespace = %s",
                    (namespace,),
                )
                count = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to delete namespace {namespace}"
            logger.warning(
                SETTINGS_DELETE_FAILED,
                namespace=namespace,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return count

    async def delete_namespace_returning_keys(
        self,
        namespace: NotBlankStr,
    ) -> tuple[NotBlankStr, ...]:
        """Atomic delete-and-return-keys for namespace clear.

        Uses ``DELETE ... RETURNING key`` so the ``get_namespace``
        snapshot and the delete cannot drift under a concurrent
        ``set`` -- the returned tuple is exactly the set of keys
        whose override row was removed by *this* call.

        Returns:
            The keys whose row was removed by this call.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM settings WHERE namespace = %s RETURNING key",
                    (namespace,),
                )
                rows = await cur.fetchall()
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to delete namespace {namespace}"
            logger.warning(
                SETTINGS_DELETE_FAILED,
                namespace=namespace,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(NotBlankStr(row[0]) for row in rows)


__all__ = ["PostgresSettingsRepository"]
