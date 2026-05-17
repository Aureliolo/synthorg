"""SQLite implementation of :class:`PrincipleOverrideRepository`."""

import contextlib
import sqlite3

import aiosqlite

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_PRINCIPLE_OVERRIDE_DELETE_FAILED,
    PERSISTENCE_PRINCIPLE_OVERRIDE_GET_FAILED,
    PERSISTENCE_PRINCIPLE_OVERRIDE_LIST_FAILED,
    PERSISTENCE_PRINCIPLE_OVERRIDE_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import (
    format_iso_utc,
    parse_iso_utc,
    validate_pagination_args,
)
from synthorg.persistence.principle_override_protocol import PrincipleOverride
from synthorg.persistence.sqlite._shared import WriteContext  # noqa: TC001

logger = get_logger(__name__)


class SQLitePrincipleOverrideRepository:
    """SQLite-backed :class:`PrincipleOverrideRepository`."""

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._write_context = write_context

    async def save(self, entity: PrincipleOverride) -> None:
        """Insert or update the override at ``scope``."""
        when = format_iso_utc(entity.created_at)
        updated_when = format_iso_utc(entity.updated_at)
        async with self._write_context():
            try:
                await self._db.execute(
                    """
                    INSERT INTO principle_overrides
                        (scope, text, restored_from, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    ON CONFLICT(scope) DO UPDATE SET
                        text = excluded.text,
                        restored_from = excluded.restored_from,
                        updated_at = excluded.updated_at
                    """,
                    (
                        str(entity.scope),
                        str(entity.text),
                        str(entity.restored_from),
                        when,
                        updated_when,
                    ),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to save principle override for scope {entity.scope!r}"
                logger.warning(
                    PERSISTENCE_PRINCIPLE_OVERRIDE_SAVE_FAILED,
                    scope=str(entity.scope),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def get(self, scope: NotBlankStr) -> PrincipleOverride | None:
        """Return the override at ``scope`` if present."""
        try:
            cursor = await self._db.execute(
                """
                SELECT scope, text, restored_from, created_at, updated_at
                FROM principle_overrides WHERE scope = ?
                """,
                (str(scope),),
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to load principle override for scope {scope!r}"
            logger.warning(
                PERSISTENCE_PRINCIPLE_OVERRIDE_GET_FAILED,
                scope=str(scope),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        return PrincipleOverride(
            scope=NotBlankStr(row[0]),
            text=NotBlankStr(row[1]),
            restored_from=NotBlankStr(row[2]),
            created_at=parse_iso_utc(row[3]),
            updated_at=parse_iso_utc(row[4]),
        )

    async def delete(self, scope: NotBlankStr) -> bool:
        """Remove the override; return ``True`` when a row was deleted."""
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "DELETE FROM principle_overrides WHERE scope = ?",
                    (str(scope),),
                )
                removed = cursor.rowcount > 0
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = f"Failed to delete principle override for scope {scope!r}"
                logger.warning(
                    PERSISTENCE_PRINCIPLE_OVERRIDE_DELETE_FAILED,
                    scope=str(scope),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return removed

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[PrincipleOverride, ...]:
        """List all overrides ordered by ``scope`` ascending."""
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_PRINCIPLE_OVERRIDE_LIST_FAILED
        )
        try:
            cursor = await self._db.execute(
                """
                SELECT scope, text, restored_from, created_at, updated_at
                FROM principle_overrides
                ORDER BY scope ASC
                LIMIT ? OFFSET ?
                """,
                (int(limit), int(offset)),
            )
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to list principle overrides"
            logger.warning(
                PERSISTENCE_PRINCIPLE_OVERRIDE_LIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(
            PrincipleOverride(
                scope=NotBlankStr(r[0]),
                text=NotBlankStr(r[1]),
                restored_from=NotBlankStr(r[2]),
                created_at=parse_iso_utc(r[3]),
                updated_at=parse_iso_utc(r[4]),
            )
            for r in rows
        )
