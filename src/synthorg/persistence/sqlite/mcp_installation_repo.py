"""SQLite-backed MCP installations repository.

Persists :class:`McpInstallation` rows in the ``mcp_installations``
table.  Bound to an open ``aiosqlite.Connection`` at construction;
the persistence backend owns connection lifecycle.
"""

import contextlib
import sqlite3

import aiosqlite

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.core.types import NotBlankStr
from synthorg.integrations.mcp_catalog.installations import McpInstallation
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.mcp_installation import (
    PERSISTENCE_MCP_INSTALLATION_DELETE_FAILED,
    PERSISTENCE_MCP_INSTALLATION_LIST_FAILED,
    PERSISTENCE_MCP_INSTALLATION_SAVE_FAILED,
)
from synthorg.persistence._shared import (
    DEFAULT_LIST_LIMIT,
    coerce_row_timestamp,
    format_iso_utc,
)
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)


def _classify_sqlite_constraint(exc: sqlite3.IntegrityError) -> str:
    """Map a SQLite IntegrityError message to a stable constraint label.

    SQLite (unlike Postgres) does not name constraints in its
    exception payload; the only identity is the human-readable
    message text. Match the message prefix and emit a stable token
    that mirrors the Postgres ``exc.diag.constraint_name`` shape so
    callers can route both backends through the same handler.

    Returns:
        Result of type ``str``.
    """
    text = str(exc).lower()
    if "foreign key" in text:
        return "mcp_installations_connection_name_fkey"
    if "unique" in text:
        return "mcp_installations_catalog_entry_id_key"
    if "not null" in text:
        return "mcp_installations_not_null"
    if "check" in text:
        return "mcp_installations_check"
    return "<unknown>"


class SQLiteMcpInstallationRepository:
    """SQLite implementation of :class:`McpInstallationRepository`."""

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._write_context = write_context

    async def save(self, installation: McpInstallation) -> None:
        """Upsert an installation row (idempotent on catalog_entry_id).

        Raises:
            ConstraintViolationError: When the upsert violates a
                database constraint, in practice the foreign key on
                ``connection_name`` when the supplied connection has
                not been persisted. Surfaces the constraint identity
                so the central exception handler can return a
                structured 4xx envelope. Parity with the Postgres
                backend, which extracts ``exc.diag.constraint_name``.
            QueryError: For all other ``sqlite3`` / ``aiosqlite``
                failures (lock timeouts, disk I/O errors, etc.).
        """
        installed_at_iso = format_iso_utc(installation.installed_at)
        async with self._write_context():
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
            except sqlite3.IntegrityError as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                # SQLite does not expose constraint names; the error
                # message text is the only identity available.
                # Surface the literal "FOREIGN KEY constraint failed"
                # so callers can route the same way as Postgres'
                # named foreign-key constraint.
                constraint = _classify_sqlite_constraint(exc)
                logger.warning(
                    PERSISTENCE_MCP_INSTALLATION_SAVE_FAILED,
                    catalog_entry_id=installation.catalog_entry_id,
                    connection_name=installation.connection_name,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    constraint=constraint,
                    backend="sqlite",
                )
                msg = (
                    f"Constraint violation saving MCP installation "
                    f"{installation.catalog_entry_id!r}"
                )
                raise ConstraintViolationError(msg, constraint=constraint) from exc
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
        """Fetch a single installation by catalog entry id.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._db.execute(
                """
                SELECT catalog_entry_id, connection_name, installed_at
                FROM mcp_installations
                WHERE catalog_entry_id = ?
                """,
                (catalog_entry_id,),
            ) as cursor:
                row = await cursor.fetchone()
        except Exception as exc:
            reraise_critical(exc)
            msg = f"Failed to fetch MCP installation {catalog_entry_id!r}"
            logger.warning(
                PERSISTENCE_MCP_INSTALLATION_LIST_FAILED,
                catalog_entry_id=catalog_entry_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        try:
            return McpInstallation(
                catalog_entry_id=NotBlankStr(row[0]),
                connection_name=(NotBlankStr(row[1]) if row[1] else None),
                installed_at=coerce_row_timestamp(row[2]),
            )
        except (ValueError, TypeError, KeyError) as exc:
            msg = f"Failed to deserialize MCP installation {catalog_entry_id!r}"
            logger.warning(
                PERSISTENCE_MCP_INSTALLATION_LIST_FAILED,
                catalog_entry_id=catalog_entry_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> tuple[McpInstallation, ...]:
        """Return up to ``limit`` recorded installations, oldest-first.

        ``limit`` defaults to 100 (matches the protocol-wide pagination
        floor) and accepts any positive integer; no upper bound is
        enforced. Callers may either pass a larger ``limit`` or loop
        with ``offset`` for cursor-style pagination.

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
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
        except Exception as exc:
            reraise_critical(exc)
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
        """Delete an installation.  Returns ``True`` if a row was removed.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        async with self._write_context():
            try:
                async with self._db.execute(
                    "DELETE FROM mcp_installations WHERE catalog_entry_id = ?",
                    (catalog_entry_id,),
                ) as cursor:
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
