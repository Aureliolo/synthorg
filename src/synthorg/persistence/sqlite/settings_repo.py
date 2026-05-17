"""SQLite implementation of the SettingsRepository protocol."""

import sqlite3
from collections.abc import Mapping, Sequence  # noqa: TC003

import aiosqlite
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
from synthorg.persistence._shared import validate_pagination_args
from synthorg.persistence.settings_protocol import (
    SettingRow,
    SettingRowKey,
)
from synthorg.persistence.sqlite._shared import WriteContext  # noqa: TC001

logger = get_logger(__name__)


class SQLiteSettingsRepository:
    """SQLite-backed namespaced settings repository.

    Settings are stored in the ``settings`` table with a composite
    primary key of ``(namespace, key)``.

    Args:
        db: An open aiosqlite connection with row_factory set.
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

    async def save(self, entity: SettingRow) -> None:
        """Persist a setting (upsert by composite key)."""
        async with self._write_context():
            try:
                await self._db.execute(
                    """\
INSERT OR REPLACE INTO settings (
    namespace, key, value, updated_at
) VALUES (
    :namespace, :key, :value, :updated_at
)""",
                    entity.model_dump(mode="json"),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
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
            cursor = await self._db.execute(
                "SELECT namespace, key, value, updated_at FROM settings "
                "WHERE namespace = ? AND key = ?",
                (namespace, key),
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
            return SettingRow.model_validate(dict(row))
        except ValidationError as exc:
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
            cursor = await self._db.execute(
                "SELECT namespace, key, value, updated_at FROM settings "
                "WHERE namespace = ? ORDER BY key",
                (namespace,),
            )
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to get settings for namespace {namespace}"
            logger.warning(
                SETTINGS_FETCH_FAILED,
                namespace=namespace,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        results: list[SettingRow] = []
        for row in rows:
            try:
                results.append(SettingRow.model_validate(dict(row)))
            except ValidationError as exc:
                msg = f"Failed to deserialize setting row {namespace}/{row['key']}"
                logger.warning(
                    SETTINGS_FETCH_FAILED,
                    namespace=namespace,
                    key=row["key"] if row else "unknown",
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
        # Validate + clamp via the shared helper (rejects limit < 1 /
        # offset < 0, caps at the repo-wide MAX_LIST_LIMIT ceiling) so
        # no inline magic ceiling and no sentinel (-1) slips through.
        effective_limit = validate_pagination_args(
            limit, offset, event=SETTINGS_FETCH_FAILED
        )
        try:
            cursor = await self._db.execute(
                "SELECT namespace, key, value, updated_at FROM settings "
                "ORDER BY namespace, key LIMIT ? OFFSET ?",
                (effective_limit, offset),
            )
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to list settings"
            logger.warning(
                SETTINGS_FETCH_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        results: list[SettingRow] = []
        for row in rows:
            try:
                results.append(SettingRow.model_validate(dict(row)))
            except ValidationError as exc:
                msg = (
                    f"Failed to deserialize setting row {row['namespace']}/{row['key']}"
                )
                logger.warning(
                    SETTINGS_FETCH_FAILED,
                    namespace=row["namespace"] if row else "unknown",
                    key=row["key"] if row else "unknown",
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
        async with self._write_context():
            try:
                if expected_updated_at is not None:
                    cursor = await self._db.execute(
                        "UPDATE settings SET value = ?, updated_at = ? "
                        "WHERE namespace = ? AND key = ? "
                        "AND updated_at = ?",
                        (
                            entity.value,
                            entity.updated_at,
                            entity.namespace,
                            entity.key,
                            expected_updated_at,
                        ),
                    )
                    await self._db.commit()
                    if cursor.rowcount == 0:
                        if expected_updated_at == "":
                            cursor = await self._db.execute(
                                "INSERT OR IGNORE INTO settings "
                                "(namespace, key, value, updated_at) "
                                "VALUES (?, ?, ?, ?)",
                                (
                                    entity.namespace,
                                    entity.key,
                                    entity.value,
                                    entity.updated_at,
                                ),
                            )
                            await self._db.commit()
                            if cursor.rowcount == 0:
                                return False
                        else:
                            return False
                else:
                    await self._db.execute(
                        "INSERT INTO settings (namespace, key, value, updated_at) "
                        "VALUES (?, ?, ?, ?) "
                        "ON CONFLICT(namespace, key) DO UPDATE SET "
                        "value=excluded.value, updated_at=excluded.updated_at",
                        (entity.namespace, entity.key, entity.value, entity.updated_at),
                    )
                    await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
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
        async with self._write_context():
            try:
                await self._db.execute("BEGIN IMMEDIATE")
                try:
                    for entity in items:
                        expected = cas_map.get((entity.namespace, entity.key))
                        if not await self._upsert_one(
                            entity,
                            expected,
                        ):
                            await self._db.rollback()
                            return False
                    await self._db.commit()
                except BaseException:
                    await self._db.rollback()
                    raise
            except (sqlite3.Error, aiosqlite.Error) as exc:
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

    async def _upsert_one(
        self,
        entity: SettingRow,
        expected: str | None,
    ) -> bool:
        """Write a single setting inside an open transaction.

        Returns ``False`` on CAS miss so the caller can rollback.
        """
        if expected is None:
            await self._db.execute(
                "INSERT INTO settings "
                "(namespace, key, value, updated_at) "
                "VALUES (?, ?, ?, ?) "
                "ON CONFLICT(namespace, key) DO UPDATE SET "
                "value=excluded.value, "
                "updated_at=excluded.updated_at",
                (entity.namespace, entity.key, entity.value, entity.updated_at),
            )
            return True
        if expected == "":
            cursor = await self._db.execute(
                "INSERT OR IGNORE INTO settings "
                "(namespace, key, value, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (entity.namespace, entity.key, entity.value, entity.updated_at),
            )
            return cursor.rowcount != 0
        cursor = await self._db.execute(
            "UPDATE settings SET value = ?, updated_at = ? "
            "WHERE namespace = ? AND key = ? "
            "AND updated_at = ?",
            (entity.value, entity.updated_at, entity.namespace, entity.key, expected),
        )
        return cursor.rowcount != 0

    async def delete(self, entity_id: SettingRowKey) -> bool:
        """Delete a setting by composite key."""
        namespace, key = entity_id
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "DELETE FROM settings WHERE namespace = ? AND key = ?",
                    (namespace, key),
                )
                await self._db.commit()
                deleted = cursor.rowcount > 0
            except (sqlite3.Error, aiosqlite.Error) as exc:
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
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "DELETE FROM settings WHERE namespace = ?",
                    (namespace,),
                )
                await self._db.commit()
                rowcount = cursor.rowcount
            except (sqlite3.Error, aiosqlite.Error) as exc:
                msg = f"Failed to delete namespace {namespace}"
                logger.warning(
                    SETTINGS_DELETE_FAILED,
                    namespace=namespace,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return rowcount

    async def delete_namespace_returning_keys(
        self,
        namespace: NotBlankStr,
    ) -> tuple[NotBlankStr, ...]:
        """Atomic delete-and-return-keys for namespace clear.

        Uses ``DELETE ... RETURNING key`` (SQLite 3.35+) so the
        ``get_namespace`` snapshot and the delete cannot drift under a
        concurrent ``set`` -- the returned tuple is exactly the set of
        keys whose override row was removed by *this* call.
        """
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "DELETE FROM settings WHERE namespace = ? RETURNING key",
                    (namespace,),
                )
                rows = await cursor.fetchall()
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                msg = f"Failed to delete namespace {namespace}"
                logger.warning(
                    SETTINGS_DELETE_FAILED,
                    namespace=namespace,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return tuple(NotBlankStr(row[0]) for row in rows)
