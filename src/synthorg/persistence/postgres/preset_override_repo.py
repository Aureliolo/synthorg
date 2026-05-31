"""Postgres repository for operator-authored preset overrides."""

from typing import TYPE_CHECKING, cast

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg.types.json import Jsonb
from pydantic import ValidationError

from synthorg.core.persistence_errors import QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_PRESET_OVERRIDE_DELETE_FAILED,
    PERSISTENCE_PRESET_OVERRIDE_QUERY_FAILED,
    PERSISTENCE_PRESET_OVERRIDE_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import normalize_utc
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.providers.enums import AuthType
from synthorg.providers.management.capability_dtos import PresetOverride

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

    from synthorg.config.schema import ProviderModelConfig
    from synthorg.core.types import NotBlankStr

logger = get_logger(__name__)


class PostgresPresetOverrideRepo:
    """Postgres implementation of :class:`PresetOverrideRepo`."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def get(self, preset_name: NotBlankStr) -> PresetOverride | None:
        """Read the override for ``preset_name``, if any.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        sql = (
            "SELECT preset_name, default_models, supported_auth_types, "
            "candidate_urls, base_url, updated_at, updated_by "
            "FROM preset_overrides WHERE preset_name = %s"
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (preset_name,))
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = "Failed to read preset override"
            logger.warning(
                PERSISTENCE_PRESET_OVERRIDE_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                preset_name=preset_name,
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        try:
            return self._row_to_override(row)
        except (
            ValidationError,
            ValueError,
            TypeError,
            KeyError,
            AttributeError,
        ) as exc:
            msg = "Failed to read preset override"
            logger.warning(
                PERSISTENCE_PRESET_OVERRIDE_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                preset_name=preset_name,
            )
            raise QueryError(msg) from exc

    async def save(self, override: PresetOverride) -> None:
        """Insert or replace the override for ``override.preset_name``.

        Raises:
            QueryError: If the database query fails.
        """
        if override.updated_at is None or override.updated_by is None:
            msg = "PresetOverride.updated_at and updated_by must be set on save"
            logger.warning(
                PERSISTENCE_PRESET_OVERRIDE_SAVE_FAILED,
                preset_name=override.preset_name,
                updated_at=override.updated_at,
                updated_by=override.updated_by,
                error=msg,
            )
            raise QueryError(msg)
        params: tuple[object, ...] = (
            override.preset_name,
            Jsonb([m.model_dump() for m in override.default_models])
            if override.default_models is not None
            else None,
            Jsonb([a.value for a in override.supported_auth_types])
            if override.supported_auth_types is not None
            else None,
            Jsonb(list(override.candidate_urls))
            if override.candidate_urls is not None
            else None,
            override.base_url,
            normalize_utc(override.updated_at),
            override.updated_by,
        )
        sql = (
            "INSERT INTO preset_overrides "
            "(preset_name, default_models, supported_auth_types, "
            "candidate_urls, base_url, updated_at, updated_by) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT (preset_name) DO UPDATE SET "
            "default_models = EXCLUDED.default_models, "
            "supported_auth_types = EXCLUDED.supported_auth_types, "
            "candidate_urls = EXCLUDED.candidate_urls, "
            "base_url = EXCLUDED.base_url, "
            "updated_at = EXCLUDED.updated_at, "
            "updated_by = EXCLUDED.updated_by"
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, params)
                await conn.commit()
        except psycopg.Error as exc:
            msg = "Failed to save preset override"
            logger.warning(
                PERSISTENCE_PRESET_OVERRIDE_SAVE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                preset_name=override.preset_name,
            )
            raise QueryError(msg) from exc

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[PresetOverride, ...]:
        """List overrides ordered by preset_name ascending.

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_PRESET_OVERRIDE_QUERY_FAILED
        )
        sql = (
            "SELECT preset_name, default_models, supported_auth_types, "
            "candidate_urls, base_url, updated_at, updated_by "
            "FROM preset_overrides "
            "ORDER BY preset_name ASC LIMIT %s OFFSET %s"
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (limit, offset))
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to list preset overrides"
            logger.warning(
                PERSISTENCE_PRESET_OVERRIDE_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        overrides: list[PresetOverride] = []
        for row in rows:
            try:
                overrides.append(self._row_to_override(row))
            except (
                ValidationError,
                ValueError,
                TypeError,
                KeyError,
                AttributeError,
            ) as exc:
                msg = "Failed to list preset overrides"
                logger.warning(
                    PERSISTENCE_PRESET_OVERRIDE_QUERY_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    preset_name=row.get("preset_name"),
                )
                raise QueryError(msg) from exc
        return tuple(overrides)

    async def delete(self, preset_name: NotBlankStr) -> bool:
        """Remove the override for ``preset_name``.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM preset_overrides WHERE preset_name = %s",
                    (preset_name,),
                )
                rowcount = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            msg = "Failed to delete preset override"
            logger.warning(
                PERSISTENCE_PRESET_OVERRIDE_DELETE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                preset_name=preset_name,
            )
            raise QueryError(msg) from exc
        return rowcount > 0

    def _row_to_override(self, row: DictRow) -> PresetOverride:
        """Deserialise a Postgres row into a ``PresetOverride``.

        Returns:
            Result of type ``PresetOverride``.
        """
        from synthorg.config.schema import (  # noqa: PLC0415
            ProviderModelConfig as _ProviderModelConfig,
        )

        def _list_or_none(raw: object) -> list[object] | None:
            return raw if isinstance(raw, list) else None

        models_raw = _list_or_none(row["default_models"])
        models: tuple[ProviderModelConfig, ...] | None = (
            tuple(_ProviderModelConfig.model_validate(m) for m in models_raw)
            if models_raw is not None
            else None
        )
        auth_types_raw = _list_or_none(row["supported_auth_types"])
        auth_types: tuple[AuthType, ...] | None = (
            tuple(AuthType(cast("str", a)) for a in auth_types_raw)
            if auth_types_raw is not None
            else None
        )
        urls_raw = _list_or_none(row["candidate_urls"])
        urls: tuple[str, ...] | None = (
            tuple(str(u) for u in urls_raw) if urls_raw is not None else None
        )
        return PresetOverride(
            preset_name=str(row["preset_name"]),
            default_models=models,
            supported_auth_types=auth_types,
            candidate_urls=urls,
            base_url=str(row["base_url"]) if row["base_url"] is not None else None,
            updated_at=normalize_utc(row["updated_at"]),
            updated_by=str(row["updated_by"]),
        )
