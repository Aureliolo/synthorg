"""SQLite repository for tracked Docker container records."""

import sqlite3
from typing import TYPE_CHECKING

import aiosqlite
from pydantic import ValidationError

from synthorg.core.persistence_errors import QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_TRACKED_CONTAINER_DELETE_FAILED,
    PERSISTENCE_TRACKED_CONTAINER_LOAD_FAILED,
    PERSISTENCE_TRACKED_CONTAINER_LOADED,
    PERSISTENCE_TRACKED_CONTAINER_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared.datetime_marshaller import (
    format_iso_utc,
    parse_iso_utc,
)
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.sqlite._shared import WriteContext  # noqa: TC001
from synthorg.persistence.tracked_container_protocol import TrackedContainerRecord

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr

logger = get_logger(__name__)


class SQLiteTrackedContainerRepository:
    """SQLite implementation of TrackedContainerRepository."""

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
        except Exception as exc:
            logger.warning(
                event,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def save(self, record: TrackedContainerRecord) -> None:
        """Insert or replace the tracking row for one container."""
        params = (
            record.container_id,
            record.sidecar_id,
            format_iso_utc(record.created_at),
        )
        async with self._write_context():
            try:
                await self._db.execute(
                    "INSERT OR REPLACE INTO tracked_containers "
                    "(container_id, sidecar_id, created_at) VALUES (?, ?, ?)",
                    params,
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._rollback_quietly(PERSISTENCE_TRACKED_CONTAINER_SAVE_FAILED)
                msg = f"Failed to save tracked container {record.container_id!r}"
                logger.warning(
                    PERSISTENCE_TRACKED_CONTAINER_SAVE_FAILED,
                    container_id=record.container_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def get(self, container_id: NotBlankStr) -> TrackedContainerRecord | None:
        """Read the tracking row for one container, or ``None`` if absent."""
        try:
            cursor = await self._db.execute(
                "SELECT container_id, sidecar_id, created_at "
                "FROM tracked_containers WHERE container_id = ?",
                (container_id,),
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to load tracked container {container_id!r}"
            logger.warning(
                PERSISTENCE_TRACKED_CONTAINER_LOAD_FAILED,
                container_id=container_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        return self._row_to_record(dict(row))

    async def delete(self, container_id: NotBlankStr) -> bool:
        """Delete the tracking row for one container."""
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "DELETE FROM tracked_containers WHERE container_id = ?",
                    (container_id,),
                )
                deleted = cursor.rowcount > 0
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._rollback_quietly(
                    PERSISTENCE_TRACKED_CONTAINER_DELETE_FAILED
                )
                msg = f"Failed to delete tracked container {container_id!r}"
                logger.warning(
                    PERSISTENCE_TRACKED_CONTAINER_DELETE_FAILED,
                    container_id=container_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return deleted

    async def load_all(self) -> tuple[TrackedContainerRecord, ...]:
        """Load every tracking row (bespoke per ADR-0001 D7)."""
        try:
            cursor = await self._db.execute(
                "SELECT container_id, sidecar_id, created_at FROM tracked_containers"
            )
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to load tracked container rows"
            logger.warning(
                PERSISTENCE_TRACKED_CONTAINER_LOAD_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        results = tuple(self._row_to_record(dict(r)) for r in rows)
        logger.debug(PERSISTENCE_TRACKED_CONTAINER_LOADED, count=len(results))
        return results

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[TrackedContainerRecord, ...]:
        """List tracked containers ordered by container_id ascending."""
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_TRACKED_CONTAINER_LOAD_FAILED
        )
        try:
            cursor = await self._db.execute(
                "SELECT container_id, sidecar_id, created_at "
                "FROM tracked_containers "
                "ORDER BY container_id ASC LIMIT ? OFFSET ?",
                (limit, offset),
            )
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to list tracked container rows"
            logger.warning(
                PERSISTENCE_TRACKED_CONTAINER_LOAD_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(self._row_to_record(dict(r)) for r in rows)

    def _row_to_record(self, row: dict[str, object]) -> TrackedContainerRecord:
        try:
            row["created_at"] = parse_iso_utc(str(row["created_at"]))
            return TrackedContainerRecord.model_validate(row)
        except (ValidationError, ValueError, TypeError, KeyError) as exc:
            msg = f"corrupt tracked_containers row {row.get('container_id')!r}"
            logger.warning(
                PERSISTENCE_TRACKED_CONTAINER_LOAD_FAILED,
                container_id=row.get("container_id"),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
