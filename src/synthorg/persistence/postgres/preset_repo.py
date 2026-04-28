"""Postgres implementation of the PresetRepository protocol.

This is the Postgres sibling of src/synthorg/persistence/sqlite/preset_repo.py.
Postgres stores config_json as native JSONB column; the protocol's
``PresetRow`` / ``PresetListRow`` contracts expose it as ``str`` (JSON
source text) and ISO 8601 strings for timestamps, so the Postgres impl
normalises ``dict`` -> ``json.dumps`` and ``datetime`` -> ``.isoformat()``
before returning rows to the caller.
"""

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

import psycopg

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
from synthorg.persistence.preset_repository import PresetListRow, PresetRow

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = get_logger(__name__)


def _normalize_config_json(value: Any) -> str:
    """Serialize a JSONB dict back to a JSON string for protocol parity.

    SQLite's ``config_json`` is stored verbatim as TEXT, so the protocol
    exposes ``str``. Postgres returns JSONB as a Python ``dict`` or
    ``list``; we re-serialise to match. Unexpected types (``int``,
    ``bytes``, ...) indicate schema drift or a broken adapter and must
    fail loudly rather than round-tripping through ``str(value)``.

    Raises:
        QueryError: If *value* is not ``str``/``dict``/``list``.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, dict | list):
        return json.dumps(value)
    msg = (
        "preset config_json from Postgres has unexpected type "
        f"{type(value).__name__}; expected str, dict, or list"
    )
    raise QueryError(msg)


def _normalize_timestamp(value: Any) -> str:
    """Return an ISO 8601 string from a ``datetime`` or passthrough ``str``.

    Raises:
        QueryError: If *value* is neither ``datetime`` nor ``str``.
    """
    if isinstance(value, str):
        return value
    if isinstance(value, datetime):
        return value.isoformat()
    msg = (
        "preset timestamp from Postgres has unexpected type "
        f"{type(value).__name__}; expected datetime or str"
    )
    raise QueryError(msg)


class PostgresPersonalityPresetRepository:
    """Postgres-backed custom personality preset repository.

    Provides CRUD operations for user-defined personality presets
    using a shared ``psycopg_pool.AsyncConnectionPool``.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

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
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """\
INSERT INTO custom_presets (name, config_json, description,
                           created_at, updated_at)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT(name) DO UPDATE SET
    config_json=EXCLUDED.config_json,
    description=EXCLUDED.description,
    updated_at=EXCLUDED.updated_at""",
                    (name, config_json, description, created_at, updated_at),
                )
                await conn.commit()
        except psycopg.Error as exc:
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
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "SELECT config_json, description, created_at, updated_at "
                    "FROM custom_presets WHERE name = %s",
                    (name,),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
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
        # ``_normalize_*`` now fails loudly when a Postgres row ships an
        # unexpected type (schema drift / adapter bug). Catch the resulting
        # ``QueryError`` here so operators see the same structured
        # ``PRESET_CUSTOM_FETCH_FAILED`` log as other fetch failures,
        # then re-raise without swallowing.
        try:
            return PresetRow(
                _normalize_config_json(row[0]),
                row[1],
                _normalize_timestamp(row[2]),
                _normalize_timestamp(row[3]),
            )
        except QueryError as exc:
            logger.warning(
                PRESET_CUSTOM_FETCH_FAILED,
                preset_name=name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                stage="row_normalization",
            )
            raise

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
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "SELECT name, config_json, description, created_at, "
                    "updated_at FROM custom_presets ORDER BY name",
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to list custom presets"
            logger.warning(
                PRESET_CUSTOM_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        try:
            result = tuple(
                PresetListRow(
                    row[0],
                    _normalize_config_json(row[1]),
                    row[2],
                    _normalize_timestamp(row[3]),
                    _normalize_timestamp(row[4]),
                )
                for row in rows
            )
        except QueryError as exc:
            logger.warning(
                PRESET_CUSTOM_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                stage="row_normalization",
            )
            raise
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
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM custom_presets WHERE name = %s",
                    (name,),
                )
                deleted = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
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
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "SELECT COUNT(*) FROM custom_presets",
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
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
