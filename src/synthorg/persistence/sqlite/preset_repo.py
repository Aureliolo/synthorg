"""SQLite repository implementation for custom personality presets."""

import asyncio
import contextlib
import sqlite3

import aiosqlite

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.preset import (
    PRESET_CUSTOM_COUNT_FAILED,
    PRESET_CUSTOM_DELETE_FAILED,
    PRESET_CUSTOM_FETCH_FAILED,
    PRESET_CUSTOM_FETCHED,
    PRESET_CUSTOM_LIST_FAILED,
    PRESET_CUSTOM_LISTED,
    PRESET_CUSTOM_SAVE_FAILED,
)
from synthorg.persistence.preset_protocol import PresetListRow, PresetRow

logger = get_logger(__name__)


class SQLitePersonalityPresetRepository:
    """SQLite-backed custom personality preset repository.

    Provides CRUD operations for user-defined personality presets
    using a shared ``aiosqlite.Connection``.

    Args:
        db: An open aiosqlite connection.
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

    async def save(
        self,
        name: NotBlankStr,
        config_json: str,
        description: str,
        created_at: str,
        updated_at: str,
    ) -> None:
        """Persist a custom preset via upsert.

        Args:
            name: Lowercase preset identifier (primary key).
            config_json: Serialized ``PersonalityConfig`` as JSON.
            description: Human-readable description.
            created_at: ISO 8601 creation timestamp.
            updated_at: ISO 8601 last-update timestamp.

        Raises:
            QueryError: If the database operation fails.
        """
        async with self._write_lock:
            try:
                await self._db.execute(
                    """\
INSERT INTO custom_presets (name, config_json, description,
                           created_at, updated_at)
VALUES (?, ?, ?, ?, ?)
ON CONFLICT(name) DO UPDATE SET
    config_json=excluded.config_json,
    description=excluded.description,
    updated_at=excluded.updated_at""",
                    (name, config_json, description, created_at, updated_at),
                )
                await self._db.commit()
            except sqlite3.Error as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to save custom preset {name!r}"
                logger.warning(
                    PRESET_CUSTOM_SAVE_FAILED,
                    preset_name=name,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def get(
        self,
        name: NotBlankStr,
    ) -> PresetRow | None:
        """Retrieve a custom preset by name.

        Args:
            name: Preset identifier.

        Returns:
            A ``PresetRow`` or ``None`` if not found.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._db.execute(
                "SELECT config_json, description, created_at, updated_at "
                "FROM custom_presets WHERE name = ?",
                (name,),
            ) as cursor:
                row = await cursor.fetchone()
        except sqlite3.Error as exc:
            msg = f"Failed to fetch custom preset {name!r}"
            logger.warning(
                PRESET_CUSTOM_FETCH_FAILED,
                preset_name=name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            logger.debug(
                PRESET_CUSTOM_FETCHED,
                preset_name=name,
                found=False,
            )
            return None
        logger.debug(PRESET_CUSTOM_FETCHED, preset_name=name, found=True)
        return PresetRow(row[0], row[1], row[2], row[3])

    async def list_all(
        self,
    ) -> tuple[PresetListRow, ...]:
        """List all custom presets ordered by name.

        Returns:
            Tuple of ``PresetListRow`` named tuples.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._db.execute(
                "SELECT name, config_json, description, created_at, "
                "updated_at FROM custom_presets ORDER BY name",
            ) as cursor:
                rows = await cursor.fetchall()
        except sqlite3.Error as exc:
            msg = "Failed to list custom presets"
            logger.warning(
                PRESET_CUSTOM_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        result = tuple(
            PresetListRow(row[0], row[1], row[2], row[3], row[4]) for row in rows
        )
        logger.debug(PRESET_CUSTOM_LISTED, count=len(result))
        return result

    async def delete(self, name: NotBlankStr) -> bool:
        """Delete a custom preset by name.

        Args:
            name: Preset identifier.

        Returns:
            ``True`` if a row was deleted, ``False`` if not found.

        Raises:
            QueryError: If the database operation fails.
        """
        async with self._write_lock:
            try:
                async with self._db.execute(
                    "DELETE FROM custom_presets WHERE name = ?",
                    (name,),
                ) as cursor:
                    deleted = cursor.rowcount > 0
                await self._db.commit()
            except sqlite3.Error as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to delete custom preset {name!r}"
                logger.warning(
                    PRESET_CUSTOM_DELETE_FAILED,
                    preset_name=name,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return deleted

    async def count(self) -> int:
        """Return the number of stored custom presets.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._db.execute(
                "SELECT COUNT(*) FROM custom_presets",
            ) as cursor:
                row = await cursor.fetchone()
        except sqlite3.Error as exc:
            msg = "Failed to count custom presets"
            logger.warning(
                PRESET_CUSTOM_COUNT_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            msg = "COUNT(*) returned no row -- database driver error"
            logger.error(PRESET_CUSTOM_COUNT_FAILED, error=msg)
            raise QueryError(msg)
        result: int = row[0]
        return result
