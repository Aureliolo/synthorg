"""SQLite repository for operator-authored preset overrides."""

import json
import sqlite3
from typing import TYPE_CHECKING

import aiosqlite

from synthorg.core.persistence_errors import QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_PRESET_OVERRIDE_DELETE_FAILED,
    PERSISTENCE_PRESET_OVERRIDE_QUERY_FAILED,
    PERSISTENCE_PRESET_OVERRIDE_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared.datetime_marshaller import (
    format_iso_utc,
    parse_iso_utc,
)
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.sqlite._shared import WriteContext
from synthorg.providers.enums import AuthType
from synthorg.providers.management.capability_dtos import PresetOverride

if TYPE_CHECKING:
    from synthorg.config.schema import ProviderModelConfig
    from synthorg.core.types import NotBlankStr

logger = get_logger(__name__)


class SQLitePresetOverrideRepo:
    """SQLite implementation of :class:`PresetOverrideRepo`.

    Args:
        db: An open ``aiosqlite.Connection``.
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
            "FROM preset_overrides WHERE preset_name = ?"
        )
        try:
            cursor = await self._db.execute(sql, (preset_name,))
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
            return self._row_to_override(dict(row))
        except QueryError as exc:
            # Already a QueryError (e.g. from ``_decode_json_list``);
            # preserve as-is so callers see a single repository
            # exception type for both query and deserialise failures.
            # Log here too: the inner raise happens inside a closure
            # whose call site does not have ``preset_name`` in scope,
            # so adding it on this boundary keeps the corrupt-row
            # context visible without forcing every helper to log.
            logger.warning(
                PERSISTENCE_PRESET_OVERRIDE_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                preset_name=preset_name,
            )
            raise
        except Exception as exc:
            # A bad row would otherwise escape as raw Pydantic /
            # enum / datetime errors, bypassing the warning log and
            # turning one corrupt row into an unexpected 500.
            # Wrap in ``QueryError`` so the repo contract holds.
            msg = f"corrupt preset_overrides row for preset {preset_name!r}"
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
            "(preset_name, default_models, supported_auth_types, "
            "candidate_urls, base_url, updated_at, updated_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)"
        )
        async with self._write_context():
            try:
                await self._db.execute(sql, params)
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._safe_rollback()
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
            "ORDER BY preset_name ASC LIMIT ? OFFSET ?"
        )
        try:
            cursor = await self._db.execute(sql, (limit, offset))
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to list preset overrides"
            logger.warning(
                PERSISTENCE_PRESET_OVERRIDE_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        overrides: list[PresetOverride] = []
        for r in rows:
            row = dict(r)
            try:
                overrides.append(self._row_to_override(row))
            except QueryError as exc:
                logger.warning(
                    PERSISTENCE_PRESET_OVERRIDE_QUERY_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    preset_name=row.get("preset_name"),
                )
                raise
            except Exception as exc:
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
        async with self._write_context():
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
                    PERSISTENCE_PRESET_OVERRIDE_DELETE_FAILED,
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
                PERSISTENCE_PRESET_OVERRIDE_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                rollback_failed=True,
            )

    def _row_to_override(self, row: dict[str, object]) -> PresetOverride:
        """Deserialise a row dict into a ``PresetOverride``.

        Returns:
            Result of type ``PresetOverride``.

        Raises:
            QueryError: If the database query fails.
        """
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

        models_raw = _decode_json_list(row["default_models"])
        models: tuple[ProviderModelConfig, ...] | None = (
            tuple(_ProviderModelConfig.model_validate(m) for m in models_raw)
            if models_raw is not None
            else None
        )
        auth_types_raw = _decode_json_list(row["supported_auth_types"])
        auth_types: tuple[AuthType, ...] | None = (
            tuple(AuthType(str(a)) for a in auth_types_raw)
            if auth_types_raw is not None
            else None
        )
        urls_raw = _decode_json_list(row["candidate_urls"])
        if urls_raw is not None and not all(isinstance(u, str) for u in urls_raw):
            msg = (
                f"preset_overrides.candidate_urls for preset "
                f"{row.get('preset_name')!r} contains non-string elements"
            )
            raise QueryError(msg)
        urls: tuple[str, ...] | None = (
            tuple(str(u) for u in urls_raw) if urls_raw is not None else None
        )

        # Validate scalar fields fail-closed: stringifying ``None``
        # via ``str(...)`` would silently produce ``"None"`` and let
        # ``get()`` return a seemingly valid ``PresetOverride`` for
        # a corrupt row.  Type-check each required scalar instead.
        preset_name_raw = row["preset_name"]
        if not isinstance(preset_name_raw, str) or preset_name_raw == "":
            msg = f"preset_overrides.preset_name corrupt or empty: {preset_name_raw!r}"
            raise QueryError(msg)
        base_url_raw = row["base_url"]
        if base_url_raw is not None and not isinstance(base_url_raw, str):
            msg = (
                f"preset_overrides.base_url for preset "
                f"{preset_name_raw!r} is not str|null: {base_url_raw!r}"
            )
            raise QueryError(msg)
        updated_at_raw = row["updated_at"]
        if not isinstance(updated_at_raw, str):
            msg = (
                f"preset_overrides.updated_at for preset "
                f"{preset_name_raw!r} is not a string: {updated_at_raw!r}"
            )
            raise QueryError(msg)
        updated_by_raw = row["updated_by"]
        if not isinstance(updated_by_raw, str) or updated_by_raw == "":
            msg = (
                f"preset_overrides.updated_by for preset "
                f"{preset_name_raw!r} is not a non-empty string: "
                f"{updated_by_raw!r}"
            )
            raise QueryError(msg)
        return PresetOverride(
            preset_name=preset_name_raw,
            default_models=models,
            supported_auth_types=auth_types,
            candidate_urls=urls,
            base_url=base_url_raw,
            updated_at=parse_iso_utc(updated_at_raw),
            updated_by=updated_by_raw,
        )
