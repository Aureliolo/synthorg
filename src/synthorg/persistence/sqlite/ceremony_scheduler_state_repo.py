"""SQLite repository for ceremony scheduler state snapshots."""

import sqlite3
from typing import TYPE_CHECKING

import aiosqlite
from pydantic import ValidationError

from synthorg.core.persistence_errors import QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_CEREMONY_STATE_DELETE_FAILED,
    PERSISTENCE_CEREMONY_STATE_LOAD_FAILED,
    PERSISTENCE_CEREMONY_STATE_LOADED,
    PERSISTENCE_CEREMONY_STATE_SAVE_FAILED,
)
from synthorg.persistence._shared.datetime_marshaller import (
    format_iso_utc,
    parse_iso_utc,
)
from synthorg.persistence.ceremony_scheduler_state_protocol import (
    CeremonySchedulerStateRecord,
)
from synthorg.persistence.sqlite._shared import WriteContext  # noqa: TC001

if TYPE_CHECKING:
    from synthorg.core.types import NotBlankStr

logger = get_logger(__name__)


class SQLiteCeremonySchedulerStateRepository:
    """SQLite implementation of CeremonySchedulerStateRepository.

    Args:
        db: An open aiosqlite connection.
        write_context: Async context manager that serializes writes on
            the shared connection.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._write_context = write_context

    async def _rollback_quietly(self, event: str) -> None:
        """Roll back the current transaction, swallowing errors."""
        try:
            await self._db.rollback()
        except MemoryError, RecursionError:
            raise
        except Exception:
            logger.warning(event, error="rollback failed")

    async def save(self, record: CeremonySchedulerStateRecord) -> None:
        """Persist a snapshot (upsert by sprint_id)."""
        params = {
            "sprint_id": record.sprint_id,
            "completion_counters_json": record.completion_counters_json,
            "fired_once_triggers_json": record.fired_once_triggers_json,
            "total_completions": record.total_completions,
            "velocity_history_json": record.velocity_history_json,
            "updated_at": format_iso_utc(record.updated_at),
        }
        async with self._write_context():
            try:
                await self._db.execute(
                    """\
INSERT OR REPLACE INTO ceremony_scheduler_state (
    sprint_id, completion_counters_json, fired_once_triggers_json,
    total_completions, velocity_history_json, updated_at
) VALUES (
    :sprint_id, :completion_counters_json, :fired_once_triggers_json,
    :total_completions, :velocity_history_json, :updated_at
)""",
                    params,
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._rollback_quietly(PERSISTENCE_CEREMONY_STATE_SAVE_FAILED)
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
        """Load a snapshot by sprint_id, or ``None`` if absent."""
        try:
            cursor = await self._db.execute(
                "SELECT sprint_id, completion_counters_json, "
                "fired_once_triggers_json, total_completions, "
                "velocity_history_json, updated_at "
                "FROM ceremony_scheduler_state "
                "WHERE sprint_id = ?",
                (sprint_id,),
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
        row_dict = dict(row)
        try:
            row_dict["updated_at"] = parse_iso_utc(str(row_dict["updated_at"]))
            record = CeremonySchedulerStateRecord.model_validate(row_dict)
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
        """Delete a snapshot by sprint_id."""
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "DELETE FROM ceremony_scheduler_state WHERE sprint_id = ?",
                    (sprint_id,),
                )
                deleted = cursor.rowcount > 0
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._rollback_quietly(PERSISTENCE_CEREMONY_STATE_DELETE_FAILED)
                msg = (
                    f"Failed to delete ceremony scheduler state for sprint "
                    f"{sprint_id!r}"
                )
                logger.warning(
                    PERSISTENCE_CEREMONY_STATE_DELETE_FAILED,
                    sprint_id=sprint_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return deleted
