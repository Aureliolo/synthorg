"""SQLite-backed MCP installations repository.

Persists :class:`McpInstallation` rows in the ``mcp_installations``
table.  Bound to an open ``aiosqlite.Connection`` at construction;
the persistence backend owns connection lifecycle.
"""

import asyncio
import contextlib
import sqlite3

import aiosqlite

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.integrations.mcp_catalog.installations import McpInstallation
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_MCP_INSTALLATION_DELETE_FAILED,
    PERSISTENCE_MCP_INSTALLATION_LIST_FAILED,
    PERSISTENCE_MCP_INSTALLATION_SAVE_FAILED,
)
from synthorg.persistence._shared import coerce_row_timestamp, format_iso_utc
from synthorg.persistence._shared.pagination import validate_pagination_args

logger = get_logger(__name__)


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
            installed_at=coerce_row_timestamp(row[2]),
        )

    async def list_items(
        self,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[McpInstallation, ...]:
        """Return up to ``limit`` recorded installations, oldest-first.

        ``limit`` defaults to 100 (matches the protocol-wide pagination
        floor) and accepts any positive integer; no upper bound is
        enforced. Callers may either pass a larger ``limit`` or loop
        with ``offset`` for cursor-style pagination.
        """
        validate_pagination_args(
            limit,
            offset,
            event=PERSISTENCE_MCP_INSTALLATION_LIST_FAILED,
        )
        sql = (
            "SELECT catalog_entry_id, connection_name, installed_at "
            "FROM mcp_installations "
            "ORDER BY installed_at ASC, catalog_entry_id ASC "
            "LIMIT ? OFFSET ?"
        )
        try:
            async with self._db.execute(sql, (limit, offset)) as cursor:
                rows = await cursor.fetchall()
            # Deserialization runs inside the same try/except so a
            # malformed persisted row surfaces under the same
            # ``PERSISTENCE_MCP_INSTALLATION_LIST_FAILED`` event +
            # ``QueryError`` envelope as a DB failure, not as a raw
            # exception that escapes the persistence boundary.
            # ``NotBlankStr`` raises ``ValueError`` on blank strings
            # and ``coerce_row_timestamp`` raises ``ValueError`` /
            # ``TypeError`` on malformed timestamps, both of which
            # would slip past a ``sqlite3.Error``-only except.
            return tuple(
                McpInstallation(
                    catalog_entry_id=NotBlankStr(row[0]),
                    connection_name=(NotBlankStr(row[1]) if row[1] else None),
                    installed_at=coerce_row_timestamp(row[2]),
                )
                for row in rows
            )
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            msg = "Failed to list mcp installations"
            logger.warning(
                PERSISTENCE_MCP_INSTALLATION_LIST_FAILED,
                limit=limit,
                offset=offset,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

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
