"""SQLite implementation of the SettingsRepository protocol."""

import asyncio
import sqlite3
from collections.abc import Mapping, Sequence  # noqa: TC003

import aiosqlite

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.settings import (
    SETTINGS_DELETE_FAILED,
    SETTINGS_FETCH_FAILED,
    SETTINGS_SET_FAILED,
    SETTINGS_VALUE_SET,
)

logger = get_logger(__name__)


class SQLiteSettingsRepository:
    """SQLite-backed namespaced settings repository.

    Settings are stored in the ``settings`` table with a composite
    primary key of ``(namespace, key)``.

    Args:
        db: An open aiosqlite connection with row_factory set.
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

    async def get(
        self,
        namespace: NotBlankStr,
        key: NotBlankStr,
    ) -> tuple[str, str] | None:
        """Retrieve (value, updated_at) or None."""
        try:
            cursor = await self._db.execute(
                "SELECT value, updated_at FROM settings "
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
        return (str(row[0]), str(row[1]))

    async def get_namespace(
        self,
        namespace: NotBlankStr,
    ) -> tuple[tuple[str, str, str], ...]:
        """Return all (key, value, updated_at) for a namespace."""
        try:
            cursor = await self._db.execute(
                "SELECT key, value, updated_at FROM settings "
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
        return tuple((str(r[0]), str(r[1]), str(r[2])) for r in rows)

    async def get_all(self) -> tuple[tuple[str, str, str, str], ...]:
        """Return all (namespace, key, value, updated_at)."""
        try:
            cursor = await self._db.execute(
                "SELECT namespace, key, value, updated_at FROM settings "
                "ORDER BY namespace, key",
            )
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to get all settings"
            logger.warning(
                SETTINGS_FETCH_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple((str(r[0]), str(r[1]), str(r[2]), str(r[3])) for r in rows)

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
            updated_at: New ``updated_at`` timestamp.
            expected_updated_at: When provided, enforces atomic
                compare-and-swap -- the row is only updated if
                the current ``updated_at`` matches.

        Returns:
            ``True`` if the write succeeded, ``False`` if the
            compare-and-swap condition was not met.
        """
        async with self._write_lock:
            try:
                if expected_updated_at is not None:
                    cursor = await self._db.execute(
                        "UPDATE settings SET value = ?, updated_at = ? "
                        "WHERE namespace = ? AND key = ? "
                        "AND updated_at = ?",
                        (value, updated_at, namespace, key, expected_updated_at),
                    )
                    await self._db.commit()
                    if cursor.rowcount == 0:
                        if expected_updated_at == "":
                            # No DB row yet -- try insert.
                            cursor = await self._db.execute(
                                "INSERT OR IGNORE INTO settings "
                                "(namespace, key, value, updated_at) "
                                "VALUES (?, ?, ?, ?)",
                                (namespace, key, value, updated_at),
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
                        (namespace, key, value, updated_at),
                    )
                    await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
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
        async with self._write_lock:
            try:
                await self._db.execute("BEGIN IMMEDIATE")
                try:
                    for namespace, key, value, updated_at in items:
                        expected = cas_map.get((str(namespace), str(key)))
                        if not await self._upsert_one(
                            namespace,
                            key,
                            value,
                            updated_at,
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
        for namespace, key, _value, _updated_at in items:
            logger.debug(
                SETTINGS_VALUE_SET,
                namespace=namespace,
                key=key,
            )
        return True

    async def _upsert_one(
        self,
        namespace: str,
        key: str,
        value: str,
        updated_at: str,
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
                (namespace, key, value, updated_at),
            )
            return True
        if expected == "":
            cursor = await self._db.execute(
                "INSERT OR IGNORE INTO settings "
                "(namespace, key, value, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (namespace, key, value, updated_at),
            )
            return cursor.rowcount != 0
        cursor = await self._db.execute(
            "UPDATE settings SET value = ?, updated_at = ? "
            "WHERE namespace = ? AND key = ? "
            "AND updated_at = ?",
            (value, updated_at, namespace, key, expected),
        )
        return cursor.rowcount != 0

    async def delete(
        self,
        namespace: NotBlankStr,
        key: NotBlankStr,
    ) -> bool:
        """Delete a setting. Return True if deleted."""
        async with self._write_lock:
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
        async with self._write_lock:
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
        async with self._write_lock:
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
