"""Postgres implementation of the SettingsRepository protocol.

Postgres stores ``updated_at`` as a native ``TIMESTAMPTZ`` column
(SQLite stores ISO 8601 strings).  The repository converts to and
from ISO strings at the boundary so the protocol surface
(SettingRow instances) is identical for both backends.
"""

from collections.abc import Mapping, Sequence  # noqa: TC003
from typing import TYPE_CHECKING, cast

import psycopg
from psycopg.rows import dict_row
from pydantic import ValidationError

from synthorg.core.types import NotBlankStr

if TYPE_CHECKING:
    from datetime import datetime

    from psycopg_pool import AsyncConnectionPool
from synthorg.core.persistence_errors import QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import (
    SETTINGS_DELETE_FAILED,
    SETTINGS_FETCH_FAILED,
    SETTINGS_SET_FAILED,
    SETTINGS_VALUE_SET,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import format_iso_utc, parse_iso_utc
from synthorg.persistence.settings_protocol import (
    SettingRow,
    SettingRowKey,
)

logger = get_logger(__name__)


class _CASConflictError(
    Exception,
):  # lint-allow: domain-error-hierarchy -- internal CAS-miss sentinel
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

    async def save(self, entity: SettingRow) -> None:
        """Persist a setting (upsert by composite key)."""
        updated_at_dt = self._safe_parse_iso(
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
        """Retrieve a setting by composite key."""
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
        """Retrieve all settings in a namespace."""
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
        """List settings across all namespaces (paginated)."""
        # Settings registry has a few hundred entries by design; the
        # 1000 cap is a defensive ceiling against misconfigured callers.
        effective_limit = min(limit, 1_000)
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

        Args:
            entity: The setting to upsert.
            expected_updated_at: When provided, enforces atomic CAS -- the
                row is only updated if the current ``updated_at`` matches.
                Empty string ``""`` signals "only insert if no row exists".

        Returns:
            ``True`` if the write succeeded, ``False`` if the CAS condition
            was not met.
        """
        updated_at_dt = self._safe_parse_iso(
            entity.updated_at, entity.namespace, entity.key
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                if expected_updated_at is not None:
                    if expected_updated_at == "":
                        await cur.execute(
                            "INSERT INTO settings "
                            "(namespace, key, value, updated_at) "
                            "VALUES (%s, %s, %s, %s) "
                            "ON CONFLICT (namespace, key) DO NOTHING",
                            (entity.namespace, entity.key, entity.value, updated_at_dt),
                        )
                    else:
                        expected_dt = self._safe_parse_iso(
                            expected_updated_at,
                            entity.namespace,
                            entity.key,
                        )
                        await cur.execute(
                            "UPDATE settings "
                            "SET value = %s, updated_at = %s "
                            "WHERE namespace = %s AND key = %s "
                            "AND updated_at = %s",
                            (
                                entity.value,
                                updated_at_dt,
                                entity.namespace,
                                entity.key,
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
                        (entity.namespace, entity.key, entity.value, updated_at_dt),
                    )
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to set setting {entity.namespace}/{entity.key}"
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
        return True

    @staticmethod
    def _safe_parse_iso(
        value: str,
        namespace: str,
        key: str,
    ) -> datetime:
        """Parse ISO timestamp, logging + raising QueryError on bad input.

        Emits a structured WARNING with ``namespace`` / ``key`` /
        ``value`` / ``error_type`` so an operator triaging a
        bad-timestamp incident has the full call-site context
        without having to grep for the raised :class:`QueryError`.
        The raw exception text is redacted via
        :func:`safe_error_description` so secret-log invariants hold
        even if the underlying ``ValueError`` carried a payload
        snippet.
        """
        try:
            return parse_iso_utc(value)
        except ValueError as exc:
            msg = f"Invalid timestamp for {namespace}/{key}: {value!r}"
            logger.warning(
                SETTINGS_SET_FAILED,
                namespace=namespace,
                key=key,
                value=value,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def set_many(
        self,
        items: Sequence[SettingRow],
        *,
        expected_updated_at_map: (Mapping[SettingRowKey, str] | None) = None,
    ) -> bool:
        """Atomically upsert multiple settings."""
        if not items:
            return True
        cas_map: Mapping[SettingRowKey, str] = expected_updated_at_map or {}
        try:
            async with self._pool.connection() as conn:
                try:
                    async with conn.transaction(), conn.cursor() as cur:
                        for entity in items:
                            updated_at_dt = self._safe_parse_iso(
                                entity.updated_at,
                                entity.namespace,
                                entity.key,
                            )
                            expected = cas_map.get((entity.namespace, entity.key))
                            if expected is None:
                                await cur.execute(
                                    "INSERT INTO settings "
                                    "(namespace, key, value, updated_at) "
                                    "VALUES (%s, %s, %s, %s) "
                                    "ON CONFLICT (namespace, key) "
                                    "DO UPDATE SET "
                                    "value = EXCLUDED.value, "
                                    "updated_at = EXCLUDED.updated_at",
                                    (
                                        entity.namespace,
                                        entity.key,
                                        entity.value,
                                        updated_at_dt,
                                    ),
                                )
                                continue
                            if expected == "":
                                await cur.execute(
                                    "INSERT INTO settings "
                                    "(namespace, key, value, updated_at) "
                                    "VALUES (%s, %s, %s, %s) "
                                    "ON CONFLICT (namespace, key) "
                                    "DO NOTHING",
                                    (
                                        entity.namespace,
                                        entity.key,
                                        entity.value,
                                        updated_at_dt,
                                    ),
                                )
                                if cur.rowcount == 0:
                                    raise _CASConflictError  # noqa: TRY301
                                continue
                            expected_dt = self._safe_parse_iso(
                                expected,
                                entity.namespace,
                                entity.key,
                            )
                            await cur.execute(
                                "UPDATE settings "
                                "SET value = %s, updated_at = %s "
                                "WHERE namespace = %s AND key = %s "
                                "AND updated_at = %s",
                                (
                                    entity.value,
                                    updated_at_dt,
                                    entity.namespace,
                                    entity.key,
                                    expected_dt,
                                ),
                            )
                            if cur.rowcount == 0:
                                raise _CASConflictError  # noqa: TRY301
                except _CASConflictError:
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
        for entity in items:
            logger.debug(
                SETTINGS_VALUE_SET,
                namespace=entity.namespace,
                key=entity.key,
            )
        return True

    async def delete(self, entity_id: SettingRowKey) -> bool:
        """Delete a setting by composite key."""
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
