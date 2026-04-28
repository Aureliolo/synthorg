"""SQLite repository for operator-authored preset overrides."""

import asyncio
import json
import sqlite3
from typing import TYPE_CHECKING

import aiosqlite

from synthorg.api.dto_provider_capabilities import PresetOverride
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_AUDIT_ENTRY_QUERY_FAILED,
)
from synthorg.persistence._shared.datetime_marshaller import (
    format_iso_utc,
    parse_iso_utc,
)
from synthorg.persistence.errors import QueryError
from synthorg.providers.enums import AuthType

if TYPE_CHECKING:
    from synthorg.config.schema import ProviderModelConfig
    from synthorg.core.types import NotBlankStr

logger = get_logger(__name__)


class SQLitePresetOverrideRepo:
    """SQLite implementation of :class:`PresetOverrideRepo`.

    Args:
        db: An open ``aiosqlite.Connection``.
        write_lock: Shared backend write lock so writes serialise with
            sibling repos sharing the same connection.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_lock: asyncio.Lock | None = None,
    ) -> None:
        self._db = db
        self._write_lock = write_lock if write_lock is not None else asyncio.Lock()

    async def get(self, preset_name: NotBlankStr) -> PresetOverride | None:
        """Read the override for ``preset_name``, if any."""
        sql = (
            "SELECT preset_name, default_models_json, supported_auth_types_json, "
            "candidate_urls_json, base_url, updated_at, updated_by "
            "FROM preset_overrides WHERE preset_name = ?"
        )
        try:
            cursor = await self._db.execute(sql, (preset_name,))
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to read preset override"
            logger.warning(
                PERSISTENCE_AUDIT_ENTRY_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                preset_name=preset_name,
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        try:
            return self._row_to_override(dict(row))
        except QueryError:
            # Already a QueryError (e.g. from ``_decode_json_list``);
            # preserve as-is so callers see a single repository
            # exception type for both query and deserialise failures.
            raise
        except Exception as exc:
            # A bad row would otherwise escape as raw Pydantic /
            # enum / datetime errors, bypassing the warning log and
            # turning one corrupt row into an unexpected 500.
            # Wrap in ``QueryError`` so the repo contract holds.
            msg = f"corrupt preset_overrides row for preset {preset_name!r}"
            logger.warning(
                PERSISTENCE_AUDIT_ENTRY_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                preset_name=preset_name,
            )
            raise QueryError(msg) from exc

    async def upsert(self, override: PresetOverride) -> PresetOverride:
        """Insert or replace the override for ``override.preset_name``."""
        if override.updated_at is None or override.updated_by is None:
            msg = "PresetOverride.updated_at and updated_by must be set on upsert"
            logger.warning(
                PERSISTENCE_AUDIT_ENTRY_QUERY_FAILED,
                preset_name=override.preset_name,
                error=msg,
            )
            raise QueryError(msg)
        params = (
            override.preset_name,
            json.dumps(
                [m.model_dump() for m in override.default_models],
                sort_keys=True,
            )
            if override.default_models is not None
            else None,
            json.dumps(
                [a.value for a in override.supported_auth_types],
                sort_keys=True,
            )
            if override.supported_auth_types is not None
            else None,
            json.dumps(list(override.candidate_urls), sort_keys=True)
            if override.candidate_urls is not None
            else None,
            override.base_url,
            format_iso_utc(override.updated_at),
            override.updated_by,
        )
        sql = (
            "INSERT OR REPLACE INTO preset_overrides "
            "(preset_name, default_models_json, supported_auth_types_json, "
            "candidate_urls_json, base_url, updated_at, updated_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)"
        )
        async with self._write_lock:
            try:
                await self._db.execute(sql, params)
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback()
                msg = "Failed to upsert preset override"
                logger.warning(
                    PERSISTENCE_AUDIT_ENTRY_QUERY_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    preset_name=override.preset_name,
                )
                raise QueryError(msg) from exc
        return override

    async def delete(self, preset_name: NotBlankStr) -> bool:
        """Remove the override for ``preset_name``."""
        async with self._write_lock:
            try:
                cursor = await self._db.execute(
                    "DELETE FROM preset_overrides WHERE preset_name = ?",
                    (preset_name,),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback()
                msg = "Failed to delete preset override"
                logger.warning(
                    PERSISTENCE_AUDIT_ENTRY_QUERY_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    preset_name=preset_name,
                )
                raise QueryError(msg) from exc
        return cursor.rowcount > 0

    async def _safe_rollback(self) -> None:
        """Best-effort rollback on the shared connection."""
        try:
            await self._db.rollback()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            logger.warning(
                PERSISTENCE_AUDIT_ENTRY_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                rollback_failed=True,
            )

    def _row_to_override(self, row: dict[str, object]) -> PresetOverride:
        """Deserialise a row dict into a ``PresetOverride``."""
        from synthorg.config.schema import (  # noqa: PLC0415
            ProviderModelConfig as _ProviderModelConfig,
        )

        def _decode_json_list(raw: object) -> list[object] | None:
            if raw is None:
                return None
            try:
                parsed = json.loads(str(raw))
            except json.JSONDecodeError as exc:
                msg = (
                    f"corrupt preset override JSON column on preset "
                    f"{row.get('preset_name')!r}"
                )
                raise QueryError(msg) from exc
            if not isinstance(parsed, list):
                # Non-list JSON (e.g. an object or scalar) is on-disk
                # corruption: silently dropping it would make the
                # field disappear from the reconstructed override and
                # hide a real schema violation.  Surface it as a
                # query failure so the bug gets investigated.
                msg = (
                    f"preset override JSON column on preset "
                    f"{row.get('preset_name')!r} is not a JSON array "
                    f"(got {type(parsed).__name__})"
                )
                raise QueryError(msg)
            return parsed

        models_raw = _decode_json_list(row["default_models_json"])
        models: tuple[ProviderModelConfig, ...] | None = (
            tuple(_ProviderModelConfig.model_validate(m) for m in models_raw)
            if models_raw is not None
            else None
        )
        auth_types_raw = _decode_json_list(row["supported_auth_types_json"])
        auth_types: tuple[AuthType, ...] | None = (
            tuple(AuthType(str(a)) for a in auth_types_raw)
            if auth_types_raw is not None
            else None
        )
        urls_raw = _decode_json_list(row["candidate_urls_json"])
        urls: tuple[str, ...] | None = (
            tuple(str(u) for u in urls_raw) if urls_raw is not None else None
        )
        return PresetOverride(
            preset_name=str(row["preset_name"]),
            default_models=models,
            supported_auth_types=auth_types,
            candidate_urls=urls,
            base_url=str(row["base_url"]) if row["base_url"] is not None else None,
            updated_at=parse_iso_utc(str(row["updated_at"])),
            updated_by=str(row["updated_by"]),
        )
