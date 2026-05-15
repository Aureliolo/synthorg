"""SQLite repository for meeting cooldown timestamps."""

import sqlite3
from typing import TYPE_CHECKING

import aiosqlite
from pydantic import ValidationError

from synthorg.core.persistence_errors import QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_MEETING_COOLDOWN_DELETE_FAILED,
    PERSISTENCE_MEETING_COOLDOWN_LOAD_FAILED,
    PERSISTENCE_MEETING_COOLDOWN_LOADED,
    PERSISTENCE_MEETING_COOLDOWN_UPSERT_FAILED,
)
from synthorg.persistence._shared.datetime_marshaller import (
    format_iso_utc,
    parse_iso_utc,
)
from synthorg.persistence.meeting_cooldown_protocol import MeetingCooldownRecord
from synthorg.persistence.sqlite._shared import WriteContext  # noqa: TC001

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr

logger = get_logger(__name__)


class SQLiteMeetingCooldownRepository:
    """SQLite implementation of MeetingCooldownRepository."""

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._write_context = write_context

    async def _rollback_quietly(self, event: str) -> None:
        try:
            await self._db.rollback()
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.warning(event, error="rollback failed")

    async def save(self, record: MeetingCooldownRecord) -> None:
        """Insert or replace the cooldown row for one meeting type."""
        params = (
            record.meeting_type_name,
            format_iso_utc(record.last_triggered_at),
        )
        async with self._write_context():
            try:
                await self._db.execute(
                    "INSERT OR REPLACE INTO meeting_cooldown "
                    "(meeting_type_name, last_triggered_at) VALUES (?, ?)",
                    params,
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._rollback_quietly(PERSISTENCE_MEETING_COOLDOWN_UPSERT_FAILED)
                msg = f"Failed to save meeting cooldown {record.meeting_type_name!r}"
                logger.warning(
                    PERSISTENCE_MEETING_COOLDOWN_UPSERT_FAILED,
                    meeting_type_name=record.meeting_type_name,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def get(self, meeting_type_name: NotBlankStr) -> MeetingCooldownRecord | None:
        """Read the cooldown row for one meeting type, or ``None`` if absent."""
        try:
            cursor = await self._db.execute(
                "SELECT meeting_type_name, last_triggered_at "
                "FROM meeting_cooldown WHERE meeting_type_name = ?",
                (meeting_type_name,),
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to load meeting cooldown {meeting_type_name!r}"
            logger.warning(
                PERSISTENCE_MEETING_COOLDOWN_LOAD_FAILED,
                meeting_type_name=meeting_type_name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        return self._row_to_record(dict(row))

    async def load_all(self) -> tuple[MeetingCooldownRecord, ...]:
        """Load every cooldown row (bespoke per ADR-0001 D7)."""
        try:
            cursor = await self._db.execute(
                "SELECT meeting_type_name, last_triggered_at FROM meeting_cooldown"
            )
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to load meeting cooldown rows"
            logger.warning(
                PERSISTENCE_MEETING_COOLDOWN_LOAD_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        results = tuple(self._row_to_record(dict(r)) for r in rows)
        logger.debug(PERSISTENCE_MEETING_COOLDOWN_LOADED, count=len(results))
        return results

    async def list_items(
        self,
        *,
        limit: int = 100,  # lint-allow: magic-numbers -- canonical ADR-0001 page size
        offset: int = 0,
    ) -> tuple[MeetingCooldownRecord, ...]:
        """List cooldown rows ordered by meeting_type_name ascending."""
        try:
            cursor = await self._db.execute(
                "SELECT meeting_type_name, last_triggered_at "
                "FROM meeting_cooldown "
                "ORDER BY meeting_type_name ASC LIMIT ? OFFSET ?",
                (limit, offset),
            )
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to list meeting cooldown rows"
            logger.warning(
                PERSISTENCE_MEETING_COOLDOWN_LOAD_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(self._row_to_record(dict(r)) for r in rows)

    def _row_to_record(self, row: dict[str, object]) -> MeetingCooldownRecord:
        try:
            row["last_triggered_at"] = parse_iso_utc(str(row["last_triggered_at"]))
            return MeetingCooldownRecord.model_validate(row)
        except (ValidationError, ValueError) as exc:
            msg = f"corrupt meeting_cooldown row {row.get('meeting_type_name')!r}"
            logger.warning(
                PERSISTENCE_MEETING_COOLDOWN_LOAD_FAILED,
                meeting_type_name=row.get("meeting_type_name"),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def delete(self, meeting_type_name: NotBlankStr) -> bool:
        """Delete the cooldown row for one meeting type."""
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "DELETE FROM meeting_cooldown WHERE meeting_type_name = ?",
                    (meeting_type_name,),
                )
                deleted = cursor.rowcount > 0
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._rollback_quietly(PERSISTENCE_MEETING_COOLDOWN_DELETE_FAILED)
                msg = f"Failed to delete meeting cooldown {meeting_type_name!r}"
                logger.warning(
                    PERSISTENCE_MEETING_COOLDOWN_DELETE_FAILED,
                    meeting_type_name=meeting_type_name,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return deleted
