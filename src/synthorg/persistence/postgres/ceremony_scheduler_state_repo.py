"""Postgres repository for ceremony scheduler state snapshots."""

from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.rows import dict_row
from pydantic import ValidationError

from synthorg.core.persistence_errors import QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_CEREMONY_STATE_DELETE_FAILED,
    PERSISTENCE_CEREMONY_STATE_LOAD_FAILED,
    PERSISTENCE_CEREMONY_STATE_LOADED,
    PERSISTENCE_CEREMONY_STATE_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import normalize_utc
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.ceremony_scheduler_state_protocol import (
    CeremonySchedulerStateRecord,
)

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

    from synthorg.core.types import NotBlankStr

logger = get_logger(__name__)


class PostgresCeremonySchedulerStateRepository:
    """Postgres implementation of CeremonySchedulerStateRepository.

    Args:
        pool: An open ``psycopg_pool.AsyncConnectionPool``.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, record: CeremonySchedulerStateRecord) -> None:
        """Persist a snapshot (upsert by sprint_id).

        Raises:
            QueryError: If the database query fails.
        """
        params: tuple[Any, ...] = (
            record.sprint_id,
            record.completion_counters_json,
            record.fired_once_triggers_json,
            record.total_completions,
            record.velocity_history_json,
            normalize_utc(record.updated_at),
        )
        sql = """\
INSERT INTO ceremony_scheduler_state (
    sprint_id, completion_counters_json, fired_once_triggers_json,
    total_completions, velocity_history_json, updated_at
) VALUES (%s, %s, %s, %s, %s, %s)
ON CONFLICT (sprint_id) DO UPDATE SET
    completion_counters_json = EXCLUDED.completion_counters_json,
    fired_once_triggers_json = EXCLUDED.fired_once_triggers_json,
    total_completions = EXCLUDED.total_completions,
    velocity_history_json = EXCLUDED.velocity_history_json,
    updated_at = EXCLUDED.updated_at
"""
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, params)
                await conn.commit()
        except psycopg.Error as exc:
            msg = (
                f"Failed to save ceremony scheduler state for sprint "
                f"{record.sprint_id!r}"
            )
            logger.warning(
                PERSISTENCE_CEREMONY_STATE_SAVE_FAILED,
                sprint_id=record.sprint_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def get(self, sprint_id: NotBlankStr) -> CeremonySchedulerStateRecord | None:
        """Load a snapshot by sprint_id, or ``None`` if absent.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        sql = (
            "SELECT sprint_id, completion_counters_json, "
            "fired_once_triggers_json, total_completions, "
            "velocity_history_json, updated_at "
            "FROM ceremony_scheduler_state "
            "WHERE sprint_id = %s"
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (sprint_id,))
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = f"Failed to load ceremony scheduler state for sprint {sprint_id!r}"
            logger.warning(
                PERSISTENCE_CEREMONY_STATE_LOAD_FAILED,
                sprint_id=sprint_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        try:
            row["updated_at"] = normalize_utc(row["updated_at"])
            record = CeremonySchedulerStateRecord.model_validate(row)
        except (ValidationError, ValueError) as exc:
            msg = f"corrupt ceremony_scheduler_state row for sprint {sprint_id!r}"
            logger.warning(
                PERSISTENCE_CEREMONY_STATE_LOAD_FAILED,
                sprint_id=sprint_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_CEREMONY_STATE_LOADED, sprint_id=sprint_id)
        return record

    async def delete(self, sprint_id: NotBlankStr) -> bool:
        """Delete a snapshot by sprint_id.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM ceremony_scheduler_state WHERE sprint_id = %s",
                    (sprint_id,),
                )
                rowcount = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to delete ceremony scheduler state for sprint {sprint_id!r}"
            logger.warning(
                PERSISTENCE_CEREMONY_STATE_DELETE_FAILED,
                sprint_id=sprint_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return rowcount > 0

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[CeremonySchedulerStateRecord, ...]:
        """List snapshots ordered by sprint_id ascending.

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_CEREMONY_STATE_LOAD_FAILED
        )
        sql = (
            "SELECT sprint_id, completion_counters_json, "
            "fired_once_triggers_json, total_completions, "
            "velocity_history_json, updated_at "
            "FROM ceremony_scheduler_state "
            "ORDER BY sprint_id ASC LIMIT %s OFFSET %s"
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (limit, offset))
                rows: list[dict[str, Any]] = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to list ceremony scheduler state snapshots"
            logger.warning(
                PERSISTENCE_CEREMONY_STATE_LOAD_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        records: list[CeremonySchedulerStateRecord] = []
        for row in rows:
            try:
                row["updated_at"] = normalize_utc(row["updated_at"])
                records.append(CeremonySchedulerStateRecord.model_validate(row))
            except (ValidationError, ValueError) as exc:
                sprint_id = str(row.get("sprint_id"))
                msg = f"corrupt ceremony_scheduler_state row for sprint {sprint_id!r}"
                logger.warning(
                    PERSISTENCE_CEREMONY_STATE_LOAD_FAILED,
                    sprint_id=sprint_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return tuple(records)
