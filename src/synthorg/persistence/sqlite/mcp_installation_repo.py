"""SQLite-backed MCP installations repository.

Persists :class:`McpInstallation` rows in the ``mcp_installations``
table.  Bound to an open ``aiosqlite.Connection`` at construction;
the persistence backend owns connection lifecycle.
"""

import asyncio
import contextlib
import sqlite3
from datetime import datetime

import aiosqlite

from synthorg.core.types import NotBlankStr
from synthorg.integrations.mcp_catalog.installations import McpInstallation
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_MCP_INSTALLATION_DELETE_FAILED,
    PERSISTENCE_MCP_INSTALLATION_SAVE_FAILED,
)
from synthorg.persistence._shared import format_iso_utc, normalize_utc, parse_iso_utc
from synthorg.persistence.errors import QueryError

logger = get_logger(__name__)


def _row_timestamp(raw: str | datetime) -> datetime:
    """Coerce a stored ``installed_at`` value to a UTC-aware datetime.

    SQLite stores TEXT, but the column type affinity returns
    ``datetime`` instances directly when ``detect_types`` is on, so the
    isinstance dispatch handles both flavours uniformly: aware or
    naive datetimes go through :func:`normalize_utc`, ISO strings go
    through :func:`parse_iso_utc`.
    """
    if isinstance(raw, datetime):
        return normalize_utc(raw)
    return parse_iso_utc(raw)


class SQLiteMcpInstallationRepository:
    """SQLite implementation of :class:`McpInstallationRepository`."""

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

    async def save(self, installation: McpInstallation) -> None:
        """Upsert an installation row (idempotent on catalog_entry_id)."""
        installed_at_iso = format_iso_utc(installation.installed_at)
        async with self._write_lock:
            try:
                await self._db.execute(
                    """
                    INSERT INTO mcp_installations (
                        catalog_entry_id, connection_name, installed_at
                    ) VALUES (?, ?, ?)
                    ON CONFLICT(catalog_entry_id) DO UPDATE SET
                        connection_name = excluded.connection_name,
                        installed_at = excluded.installed_at
                    """,
                    (
                        installation.catalog_entry_id,
                        installation.connection_name,
                        installed_at_iso,
                    ),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = (
                    f"Failed to save mcp installation {installation.catalog_entry_id!r}"
                )
                logger.warning(
                    PERSISTENCE_MCP_INSTALLATION_SAVE_FAILED,
                    catalog_entry_id=installation.catalog_entry_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        # Mutation-audit success logging belongs in the service layer
        # per CLAUDE.md persistence-boundary; only error-path logging
        # lives here (CLAUDE.md "all error paths must log at WARNING
        # or ERROR with context before raising").

    async def get(
        self,
        catalog_entry_id: NotBlankStr,
    ) -> McpInstallation | None:
        """Fetch a single installation by catalog entry id."""
        async with self._db.execute(
            """
            SELECT catalog_entry_id, connection_name, installed_at
            FROM mcp_installations
            WHERE catalog_entry_id = ?
            """,
            (catalog_entry_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return None
        return McpInstallation(
            catalog_entry_id=NotBlankStr(row[0]),
            connection_name=(NotBlankStr(row[1]) if row[1] else None),
            installed_at=_row_timestamp(row[2]),
        )

    async def list_all(self) -> tuple[McpInstallation, ...]:
        """List all recorded installations, oldest-first."""
        async with self._db.execute(
            """
            SELECT catalog_entry_id, connection_name, installed_at
            FROM mcp_installations
            ORDER BY installed_at ASC
            """,
        ) as cursor:
            rows = await cursor.fetchall()
        return tuple(
            McpInstallation(
                catalog_entry_id=NotBlankStr(row[0]),
                connection_name=(NotBlankStr(row[1]) if row[1] else None),
                installed_at=_row_timestamp(row[2]),
            )
            for row in rows
        )

    async def delete(self, catalog_entry_id: NotBlankStr) -> bool:
        """Delete an installation.  Returns ``True`` if a row was removed."""
        async with self._write_lock:
            try:
                cursor = await self._db.execute(
                    "DELETE FROM mcp_installations WHERE catalog_entry_id = ?",
                    (catalog_entry_id,),
                )
                deleted = cursor.rowcount > 0
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to delete mcp installation {catalog_entry_id!r}"
                logger.warning(
                    PERSISTENCE_MCP_INSTALLATION_DELETE_FAILED,
                    catalog_entry_id=catalog_entry_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return deleted
