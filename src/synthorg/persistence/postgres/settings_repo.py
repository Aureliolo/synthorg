"""Postgres implementation of the SettingsRepository protocol.

Postgres stores ``updated_at`` as a native ``TIMESTAMPTZ`` column
(SQLite stores ISO 8601 strings).  The repository converts to and
from ISO strings at the boundary so the protocol surface --
``tuple[str, str]`` -- is identical for both backends.
"""

from collections.abc import Mapping, Sequence  # noqa: TC003
from typing import TYPE_CHECKING, cast

import psycopg
from psycopg.rows import dict_row

from synthorg.core.types import NotBlankStr

if TYPE_CHECKING:
    from datetime import datetime

    from psycopg_pool import AsyncConnectionPool
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import (
    SETTINGS_DELETE_FAILED,
    SETTINGS_FETCH_FAILED,
    SETTINGS_SET_FAILED,
    SETTINGS_VALUE_SET,
)
from synthorg.persistence._shared import format_iso_utc, parse_iso_utc
from synthorg.persistence.errors import QueryError

logger = get_logger(__name__)


class _CASConflict(Exception):  # noqa: N818
    """Internal sentinel -- raised inside transactions to signal CAS miss.

    Caught immediately by ``set_many`` to convert the exception into a
    ``False`` return.  Never escapes the repository.
    """


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

    async def get(
        self,
        namespace: NotBlankStr,
        key: NotBlankStr,
    ) -> tuple[str, str] | None:
        """Retrieve (value, updated_at) or None."""
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT value, updated_at FROM settings "
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
        return (str(row["value"]), format_iso_utc(cast("datetime", row["updated_at"])))

    async def get_namespace(
        self,
        namespace: NotBlankStr,
    ) -> tuple[tuple[str, str, str], ...]:
        """Return all (key, value, updated_at) for a namespace."""
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT key, value, updated_at FROM settings "
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
        return tuple(
            (
                str(r["key"]),
                str(r["value"]),
                format_iso_utc(cast("datetime", r["updated_at"])),
            )
            for r in rows
        )

    async def get_all(self) -> tuple[tuple[str, str, str, str], ...]:
        """Return all (namespace, key, value, updated_at)."""
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT namespace, key, value, updated_at FROM settings "
                    "ORDER BY namespace, key"
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to get all settings"
            logger.warning(
                SETTINGS_FETCH_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(
            (
                str(r["namespace"]),
                str(r["key"]),
                str(r["value"]),
                format_iso_utc(cast("datetime", r["updated_at"])),
            )
            for r in rows
        )

    async def set(
        self,
        namespace: NotBlankStr,
        key: NotBlankStr,
        value: str,
        updated_at: str,
        *,
        expected_updated_at: str | None = None,
    ) -> bool:
        """Upsert a setting.

        Args:
            namespace: Setting namespace.
            key: Setting key.
            value: Serialized setting value.
            updated_at: New ``updated_at`` timestamp (ISO 8601 string).
            expected_updated_at: When provided, enforces atomic
                compare-and-swap -- the row is only updated if the
                current ``updated_at`` matches.  An empty string
                signals "only insert if no row exists".

        Returns:
            ``True`` if the write succeeded, ``False`` if the
            compare-and-swap condition was not met.
        """
        updated_at_dt = parse_iso_utc(updated_at)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                if expected_updated_at is not None:
                    if expected_updated_at == "":
                        await cur.execute(
                            "INSERT INTO settings "
                            "(namespace, key, value, updated_at) "
                            "VALUES (%s, %s, %s, %s) "
                            "ON CONFLICT (namespace, key) DO NOTHING",
                            (namespace, key, value, updated_at_dt),
                        )
                    else:
                        expected_dt = parse_iso_utc(expected_updated_at)
                        await cur.execute(
                            "UPDATE settings "
                            "SET value = %s, updated_at = %s "
                            "WHERE namespace = %s AND key = %s "
                            "AND updated_at = %s",
                            (
                                value,
                                updated_at_dt,
                                namespace,
                                key,
                                expected_dt,
                            ),
                        )
                    if cur.rowcount == 0:
                        await conn.commit()
                        return False
                else:
                    await cur.execute(
                        "INSERT INTO settings "
                        "(namespace, key, value, updated_at) "
                        "VALUES (%s, %s, %s, %s) "
                        "ON CONFLICT (namespace, key) DO UPDATE SET "
                        "value = EXCLUDED.value, "
                        "updated_at = EXCLUDED.updated_at",
                        (namespace, key, value, updated_at_dt),
                    )
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to set setting {namespace}/{key}"
            logger.warning(
                SETTINGS_SET_FAILED,
                namespace=namespace,
                key=key,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(
            SETTINGS_VALUE_SET,
            namespace=namespace,
            key=key,
        )
        return True

    @staticmethod
    def _safe_parse_iso(
        value: str,
        namespace: str,
        key: str,
    ) -> datetime:
        """Parse ISO timestamp, raising QueryError on bad input."""
        try:
            return parse_iso_utc(value)
        except ValueError as exc:
            msg = f"Invalid timestamp for {namespace}/{key}: {value!r}"
            raise QueryError(msg) from exc

    async def set_many(
        self,
        items: Sequence[tuple[NotBlankStr, NotBlankStr, str, str]],
        *,
        expected_updated_at_map: (Mapping[tuple[str, str], str] | None) = None,
    ) -> bool:
        """Atomically upsert multiple settings (see protocol docstring)."""
        if not items:
            return True
        cas_map: Mapping[tuple[str, str], str] = expected_updated_at_map or {}
        try:
            async with self._pool.connection() as conn:
                try:
                    async with conn.transaction(), conn.cursor() as cur:
                        for namespace, key, value, updated_at in items:
                            updated_at_dt = self._safe_parse_iso(
                                updated_at,
                                str(namespace),
                                str(key),
                            )
                            expected = cas_map.get((str(namespace), str(key)))
                            if expected is None:
                                await cur.execute(
                                    "INSERT INTO settings "
                                    "(namespace, key, value, updated_at) "
                                    "VALUES (%s, %s, %s, %s) "
                                    "ON CONFLICT (namespace, key) "
                                    "DO UPDATE SET "
                                    "value = EXCLUDED.value, "
                                    "updated_at = EXCLUDED.updated_at",
                                    (namespace, key, value, updated_at_dt),
                                )
                                continue
                            if expected == "":
                                await cur.execute(
                                    "INSERT INTO settings "
                                    "(namespace, key, value, updated_at) "
                                    "VALUES (%s, %s, %s, %s) "
                                    "ON CONFLICT (namespace, key) "
                                    "DO NOTHING",
                                    (namespace, key, value, updated_at_dt),
                                )
                                if cur.rowcount == 0:
                                    raise _CASConflict  # noqa: TRY301
                                continue
                            expected_dt = parse_iso_utc(expected)
                            await cur.execute(
                                "UPDATE settings "
                                "SET value = %s, updated_at = %s "
                                "WHERE namespace = %s AND key = %s "
                                "AND updated_at = %s",
                                (
                                    value,
                                    updated_at_dt,
                                    namespace,
                                    key,
                                    expected_dt,
                                ),
                            )
                            if cur.rowcount == 0:
                                raise _CASConflict  # noqa: TRY301
                except _CASConflict:
                    return False
        except psycopg.Error as exc:
            msg = "Failed to set_many settings"
            logger.warning(
                SETTINGS_SET_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                item_count=len(items),
            )
            raise QueryError(msg) from exc
        for namespace, key, _value, _updated_at in items:
            logger.debug(
                SETTINGS_VALUE_SET,
                namespace=namespace,
                key=key,
            )
        return True

    async def delete(
        self,
        namespace: NotBlankStr,
        key: NotBlankStr,
    ) -> bool:
        """Delete a setting. Return True if deleted."""
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
        """Delete all settings in a namespace. Return count."""
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
