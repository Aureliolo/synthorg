"""Postgres implementation of the PresetRepository protocol.

This is the Postgres sibling of src/synthorg/persistence/sqlite/preset_repo.py.
Postgres stores config_json as native JSONB column; the protocol's
``Preset`` contract exposes it as ``str`` (JSON source text) and ISO 8601
strings for timestamps, so the Postgres impl normalises ``dict`` ->
``json.dumps`` and ``datetime`` -> ``.isoformat()`` before returning rows
to the caller.
"""

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.types.json import Jsonb

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

    Returns:
        Result of type ``str``.
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


def _config_json_to_jsonb(raw: str) -> Jsonb:
    """Adapt the protocol's JSON-text ``config_json`` for a JSONB column.

    The ``Preset`` contract carries ``config_json`` as JSON source text
    (SQLite stores it verbatim as TEXT). Postgres' column is JSONB, so
    passing the raw ``str`` would store it as a JSON *string scalar*
    rather than the underlying object, corrupting JSONB queries and the
    read-side ``dict``/``list`` round-trip. Parse then wrap so the value
    is stored structurally.

    Raises:
        QueryError: If *raw* is not valid JSON.

    Returns:
        Result of type ``Jsonb``.
    """
    try:
        return Jsonb(json.loads(raw))
    except (json.JSONDecodeError, ValueError) as exc:
        msg = "preset config_json is not valid JSON"
        raise QueryError(msg) from exc


def _normalize_timestamp(value: Any) -> str:
    """Return an ISO 8601 string from a ``datetime`` or passthrough ``str``.

    Raises:
        QueryError: If *value* is neither ``datetime`` nor ``str``.

    Returns:
        Result of type ``str``.
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

    async def save(self, entity: Preset) -> None:
        """Persist a custom preset via upsert.

        Args:
            entity: The preset to persist.

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
                    (
                        entity.name,
                        _config_json_to_jsonb(entity.config_json),
                        entity.description,
                        entity.created_at,
                        entity.updated_at,
                    ),
                )
                await conn.commit()
        except psycopg.Error as exc:
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
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "SELECT name, config_json, description, created_at, updated_at "
                    "FROM custom_presets WHERE name = %s",
                    (entity_id,),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
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
        try:
            return Preset(
                name=row[0],
                config_json=_normalize_config_json(row[1]),
                description=row[2],
                created_at=_normalize_timestamp(row[3]),
                updated_at=_normalize_timestamp(row[4]),
            )
        except QueryError as exc:
            logger.warning(
                PRESET_CUSTOM_FETCH_FAILED,
                preset_name=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                stage="row_normalization",
            )
            raise

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
            QueryError: If the database query fails or pagination is
                out of range.

        Returns:
            The matching entities.
        """
        limit = validate_pagination_args(limit, offset, event=PRESET_CUSTOM_LIST_FAILED)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "SELECT name, config_json, description, created_at, "
                    "updated_at FROM custom_presets ORDER BY name LIMIT %s OFFSET %s",
                    (limit, offset),
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
                Preset(
                    name=row[0],
                    config_json=_normalize_config_json(row[1]),
                    description=row[2],
                    created_at=_normalize_timestamp(row[3]),
                    updated_at=_normalize_timestamp(row[4]),
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

        Returns:
            The matching entities.
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

    async def delete(self, entity_id: NotBlankStr) -> bool:
        """Delete a custom preset by name.

        Args:
            entity_id: Preset identifier (name).

        Returns:
            ``True`` if a row was deleted, ``False`` if not found.

        Raises:
            QueryError: If the database operation fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM custom_presets WHERE name = %s",
                    (entity_id,),
                )
                deleted = cur.rowcount > 0
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to delete custom preset {entity_id!r}"
            logger.warning(
                PRESET_CUSTOM_DELETE_FAILED,
                preset_name=entity_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

        return deleted
