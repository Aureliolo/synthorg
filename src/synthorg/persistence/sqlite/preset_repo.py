"""SQLite repository implementation for custom personality presets."""

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
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import validate_pagination_args
from synthorg.persistence.preset_protocol import Preset, PresetFilterSpec
from synthorg.persistence.sqlite._shared import WriteContext  # noqa: TC001

logger = get_logger(__name__)


class SQLitePersonalityPresetRepository:
    """SQLite-backed custom personality preset repository.

    Provides CRUD operations for user-defined personality presets
    using a shared ``aiosqlite.Connection``.

    Args:
        db: An open aiosqlite connection.
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

    async def save(self, entity: Preset) -> None:
        """Persist a custom preset via upsert.

        Args:
            entity: The preset to persist.

        Raises:
            QueryError: If the database operation fails.
        """
        async with self._write_context():
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
                    (
                        entity.name,
                        entity.config_json,
                        entity.description,
                        entity.created_at,
                        entity.updated_at,
                    ),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to save custom preset {entity.name!r}"
                logger.warning(
                    PRESET_CUSTOM_SAVE_FAILED,
                    preset_name=entity.name,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def get(self, entity_id: NotBlankStr) -> Preset | None:
        """Retrieve a custom preset by name.

        Args:
            entity_id: Preset identifier (name).

        Returns:
            A ``Preset`` or ``None`` if not found.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._db.execute(
                "SELECT name, config_json, description, created_at, updated_at "
                "FROM custom_presets WHERE name = ?",
                (entity_id,),
            ) as cursor:
                row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to fetch custom preset {entity_id!r}"
            logger.warning(
                PRESET_CUSTOM_FETCH_FAILED,
                preset_name=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            logger.debug(
                PRESET_CUSTOM_FETCHED,
                preset_name=entity_id,
                found=False,
            )
            return None
        logger.debug(PRESET_CUSTOM_FETCHED, preset_name=entity_id, found=True)
        return Preset(
            name=row[0],
            config_json=row[1],
            description=row[2],
            created_at=row[3],
            updated_at=row[4],
        )

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Preset, ...]:
        """List custom presets ordered by name.

        Args:
            limit: Maximum presets to return.
            offset: Rows to skip before the window.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(limit, offset, event=PRESET_CUSTOM_LIST_FAILED)
        try:
            async with self._db.execute(
                "SELECT name, config_json, description, created_at, "
                "updated_at FROM custom_presets ORDER BY name LIMIT ? OFFSET ?",
                (limit, offset),
            ) as cursor:
                rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to list custom presets"
            logger.warning(
                PRESET_CUSTOM_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        result = tuple(
            Preset(
                name=row[0],
                config_json=row[1],
                description=row[2],
                created_at=row[3],
                updated_at=row[4],
            )
            for row in rows
        )
        logger.debug(PRESET_CUSTOM_LISTED, count=len(result))
        return result

    async def query(
        self,
        filter_spec: PresetFilterSpec,  # noqa: ARG002
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Preset, ...]:
        """List custom presets matching the filter spec.

        Args:
            filter_spec: Filter criteria (currently unused, reserved for future).
            limit: Maximum presets to return.
            offset: Rows to skip before the window.

        Raises:
            QueryError: If the database query fails.
        """
        return await self.list_items(limit=limit, offset=offset)

    async def count(self, filter_spec: PresetFilterSpec) -> int:  # noqa: ARG002
        """Count custom presets matching the filter spec.

        Args:
            filter_spec: Filter criteria (currently unused, reserved for future).

        Returns:
            Number of matching presets.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._db.execute(
                "SELECT COUNT(*) FROM custom_presets",
            ) as cursor:
                row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a custom preset by name.

        Args:
            entity_id: Preset identifier (name).

        Returns:
            ``True`` if a row was deleted, ``False`` if not found.

        Raises:
            QueryError: If the database operation fails.
        """
        async with self._write_context():
            try:
                async with self._db.execute(
                    "DELETE FROM custom_presets WHERE name = ?",
                    (entity_id,),
                ) as cursor:
                    deleted = cursor.rowcount > 0
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to delete custom preset {entity_id!r}"
                logger.warning(
                    PRESET_CUSTOM_DELETE_FAILED,
                    preset_name=entity_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return deleted
