"""Postgres repository for meeting cooldown timestamps."""

from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.rows import dict_row
from pydantic import ValidationError

from synthorg.core.persistence_errors import QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_MEETING_COOLDOWN_DELETE_FAILED,
    PERSISTENCE_MEETING_COOLDOWN_LOAD_FAILED,
    PERSISTENCE_MEETING_COOLDOWN_LOADED,
    PERSISTENCE_MEETING_COOLDOWN_UPSERT_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import normalize_utc
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.meeting_cooldown_protocol import MeetingCooldownRecord

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

    from synthorg.core.types import NotBlankStr

logger = get_logger(__name__)


class PostgresMeetingCooldownRepository:
    """Postgres implementation of MeetingCooldownRepository."""

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, record: MeetingCooldownRecord) -> None:
        """Insert or replace the cooldown row for one meeting type.

        Raises:
            QueryError: If the database query fails.
        """
        params: tuple[Any, ...] = (
            record.meeting_type_name,
            normalize_utc(record.last_triggered_at),
        )
        sql = (
            "INSERT INTO meeting_cooldown "
            "(meeting_type_name, last_triggered_at) "
            "VALUES (%s, %s) "
            "ON CONFLICT (meeting_type_name) DO UPDATE SET "
            "last_triggered_at = EXCLUDED.last_triggered_at"
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, params)
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to save meeting cooldown {record.meeting_type_name!r}"
            logger.warning(
                PERSISTENCE_MEETING_COOLDOWN_UPSERT_FAILED,
                meeting_type_name=record.meeting_type_name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def get(self, meeting_type_name: NotBlankStr) -> MeetingCooldownRecord | None:
        """Read the cooldown row for one meeting type, or ``None`` if absent.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT meeting_type_name, last_triggered_at "
                    "FROM meeting_cooldown WHERE meeting_type_name = %s",
                    (meeting_type_name,),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
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
        return self._row_to_record(row)

    async def load_all(self) -> tuple[MeetingCooldownRecord, ...]:
        """Load every cooldown row (bespoke per ADR-0001 D7).

        Returns:
            Tuple of matching rows; empty when no rows match.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT meeting_type_name, last_triggered_at FROM meeting_cooldown"
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to load meeting cooldown rows"
            logger.warning(
                PERSISTENCE_MEETING_COOLDOWN_LOAD_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        results = tuple(self._row_to_record(r) for r in rows)
        logger.debug(PERSISTENCE_MEETING_COOLDOWN_LOADED, count=len(results))
        return results

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[MeetingCooldownRecord, ...]:
        """List cooldown rows ordered by meeting_type_name ascending.

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_MEETING_COOLDOWN_LOAD_FAILED
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT meeting_type_name, last_triggered_at "
                    "FROM meeting_cooldown "
                    "ORDER BY meeting_type_name ASC LIMIT %s OFFSET %s",
                    (limit, offset),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to list meeting cooldown rows"
            logger.warning(
                PERSISTENCE_MEETING_COOLDOWN_LOAD_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(self._row_to_record(r) for r in rows)

    def _row_to_record(self, row: dict[str, Any]) -> MeetingCooldownRecord:
        """Row to record.

        Returns:
            Result of type ``MeetingCooldownRecord``.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            row["last_triggered_at"] = normalize_utc(row["last_triggered_at"])
            return MeetingCooldownRecord.model_validate(row)
        except (ValidationError, ValueError, TypeError, KeyError) as exc:
            msg = f"corrupt meeting_cooldown row {row.get('meeting_type_name')!r}"
            logger.warning(
                PERSISTENCE_MEETING_COOLDOWN_LOAD_FAILED,
                meeting_type_name=row.get("meeting_type_name"),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def delete(self, meeting_type_name: NotBlankStr) -> bool:
        """Delete the cooldown row for one meeting type.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM meeting_cooldown WHERE meeting_type_name = %s",
                    (meeting_type_name,),
                )
                rowcount = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to delete meeting cooldown {meeting_type_name!r}"
            logger.warning(
                PERSISTENCE_MEETING_COOLDOWN_DELETE_FAILED,
                meeting_type_name=meeting_type_name,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return rowcount > 0
