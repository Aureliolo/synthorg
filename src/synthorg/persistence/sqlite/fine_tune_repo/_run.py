"""SQLite repository for fine-tuning pipeline runs."""

import json
import sqlite3
from datetime import UTC, datetime
from uuid import UUID

import aiosqlite

from synthorg.core.persistence_errors import MalformedRowError, QueryError
from synthorg.memory.embedding.fine_tune import FineTuneStage
from synthorg.memory.embedding.fine_tune_models import FineTuneRun, FineTuneRunConfig
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import (
    MEMORY_FINE_TUNE_INTERRUPTED,
    MEMORY_FINE_TUNE_PERSIST_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import coerce_row_timestamp, format_iso_utc
from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)

_ACTIVE_STAGES = tuple(
    s.value
    for s in FineTuneStage
    if s
    not in {
        FineTuneStage.IDLE,
        FineTuneStage.COMPLETE,
        FineTuneStage.FAILED,
    }
)

_MAX_LIST_LIMIT: int = 1_000


def _run_from_row(row: aiosqlite.Row) -> FineTuneRun:
    """Build a ``FineTuneRun`` from a database row.

    Returns:
        Result of type ``FineTuneRun``.

    Raises:
        MalformedRowError: If the row contains invalid data.
    """
    try:
        config = FineTuneRunConfig.model_validate_json(row["config_json"])
        stages = tuple(json.loads(row["stages_completed"]))
        return FineTuneRun(
            id=UUID(str(row["id"])),
            stage=FineTuneStage(row["stage"]),
            progress=row["progress"],
            error=row["error"],
            config=config,
            started_at=coerce_row_timestamp(row["started_at"]),
            updated_at=coerce_row_timestamp(row["updated_at"]),
            completed_at=(
                coerce_row_timestamp(row["completed_at"])
                if row["completed_at"] is not None
                else None
            ),
            stages_completed=stages,
        )
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        msg = f"Corrupt fine-tune run row: {safe_error_description(exc)}"
        raise MalformedRowError(msg) from exc


class SQLiteFineTuneRunRepository:
    """SQLite-backed fine-tuning run repository.

    Args:
        db: An open aiosqlite connection with row_factory set.
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

    async def save(self, entity: FineTuneRun) -> None:
        """Upsert a run by id (idempotent semantics).

        Raises:
            QueryError: If the database query fails.
        """
        run = entity
        async with self._write_context():
            try:
                await self._db.execute(
                    "INSERT INTO fine_tune_runs "
                    "(id, stage, progress, error, config_json, "
                    "started_at, updated_at, completed_at, "
                    "stages_completed) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET "
                    "stage = excluded.stage, "
                    "progress = excluded.progress, "
                    "error = excluded.error, "
                    "config_json = excluded.config_json, "
                    "updated_at = excluded.updated_at, "
                    "completed_at = excluded.completed_at, "
                    "stages_completed = excluded.stages_completed",
                    (
                        str(run.id),
                        run.stage.value,
                        run.progress,
                        run.error,
                        run.config.model_dump_json(),
                        format_iso_utc(run.started_at),
                        format_iso_utc(run.updated_at),
                        (
                            format_iso_utc(run.completed_at)
                            if run.completed_at
                            else None
                        ),
                        json.dumps(list(run.stages_completed)),
                    ),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                msg = f"Failed to save fine-tune run {run.id!s}"
                logger.warning(
                    MEMORY_FINE_TUNE_PERSIST_FAILED,
                    run_id=str(run.id),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def get(self, entity_id: str) -> FineTuneRun | None:
        """Retrieve a run by id.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        run_id = entity_id
        try:
            cursor = await self._db.execute(
                "SELECT * FROM fine_tune_runs WHERE id = ?",
                (run_id,),
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to get fine-tune run {run_id}"
            logger.warning(
                MEMORY_FINE_TUNE_PERSIST_FAILED,
                run_id=run_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        return _run_from_row(row)

    async def get_active_run(self) -> FineTuneRun | None:
        """Get the currently active run (if any).

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        placeholders = ", ".join("?" for _ in _ACTIVE_STAGES)
        query = (
            f"SELECT * FROM fine_tune_runs "  # noqa: S608
            f"WHERE stage IN ({placeholders}) "
            "ORDER BY started_at DESC LIMIT 1"
        )
        try:
            cursor = await self._db.execute(query, _ACTIVE_STAGES)
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to query active fine-tune run"
            logger.warning(
                MEMORY_FINE_TUNE_PERSIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        return _run_from_row(row)

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[FineTuneRun, ...]:
        """List runs in ascending id order (paginated).

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        limit = min(max(limit, 1), _MAX_LIST_LIMIT)
        offset = max(offset, 0)
        try:
            cursor = await self._db.execute(
                "SELECT * FROM fine_tune_runs ORDER BY id ASC LIMIT ? OFFSET ?",
                (limit, offset),
            )
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to list fine-tune runs"
            logger.warning(
                MEMORY_FINE_TUNE_PERSIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(_run_from_row(r) for r in rows)

    async def list_items_page(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[tuple[FineTuneRun, ...], int]:
        """List runs ordered by start time descending with total count.

        Returns:
            Tuple of (runs, total_count).

        Raises:
            QueryError: If the database query fails.
        """
        limit = min(max(limit, 1), _MAX_LIST_LIMIT)
        offset = max(offset, 0)
        try:
            count_cursor = await self._db.execute(
                "SELECT COUNT(*) FROM fine_tune_runs",
            )
            count_row = await count_cursor.fetchone()
            total = count_row[0] if count_row else 0

            cursor = await self._db.execute(
                "SELECT * FROM fine_tune_runs "
                "ORDER BY started_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to list fine-tune runs"
            logger.warning(
                MEMORY_FINE_TUNE_PERSIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(_run_from_row(r) for r in rows), total

    async def delete(self, entity_id: str) -> bool:
        """Delete a run by id.

        Returns:
            ``True`` if deleted, ``False`` if not found.

        Raises:
            QueryError: If the database query fails.
        """
        run_id = entity_id
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "DELETE FROM fine_tune_runs WHERE id = ?",
                    (run_id,),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                msg = f"Failed to delete fine-tune run {run_id}"
                logger.warning(
                    MEMORY_FINE_TUNE_PERSIST_FAILED,
                    run_id=run_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
            else:
                return cursor.rowcount > 0

    async def update_run(self, run: FineTuneRun) -> None:
        """Update all mutable fields for a run.

        Raises:
            QueryError: If the database query fails.
        """
        async with self._write_context():
            try:
                await self._db.execute(
                    "UPDATE fine_tune_runs SET "
                    "stage = ?, progress = ?, error = ?, "
                    "config_json = ?, "
                    "updated_at = ?, completed_at = ?, "
                    "stages_completed = ? "
                    "WHERE id = ?",
                    (
                        run.stage.value,
                        run.progress,
                        run.error,
                        run.config.model_dump_json(),
                        format_iso_utc(run.updated_at),
                        (
                            format_iso_utc(run.completed_at)
                            if run.completed_at
                            else None
                        ),
                        json.dumps(list(run.stages_completed)),
                        str(run.id),
                    ),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                msg = f"Failed to update fine-tune run {run.id!s}"
                logger.warning(
                    MEMORY_FINE_TUNE_PERSIST_FAILED,
                    run_id=str(run.id),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def mark_interrupted(self) -> int:
        """Mark all active runs as FAILED on startup recovery.

        Returns:
            Number of runs marked as interrupted.

        Raises:
            QueryError: If the database query fails.
        """
        placeholders = ", ".join("?" for _ in _ACTIVE_STAGES)
        now = format_iso_utc(datetime.now(UTC))
        query = (
            f"UPDATE fine_tune_runs SET "  # noqa: S608
            f"stage = ?, error = ?, updated_at = ?, completed_at = ? "
            f"WHERE stage IN ({placeholders})"
        )
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    query,
                    (
                        FineTuneStage.FAILED.value,
                        "interrupted by restart",
                        now,
                        now,
                        *_ACTIVE_STAGES,
                    ),
                )
                await self._db.commit()
                count = cursor.rowcount
            except (sqlite3.Error, aiosqlite.Error) as exc:
                msg = "Failed to mark interrupted fine-tune runs"
                logger.warning(
                    MEMORY_FINE_TUNE_PERSIST_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        if count > 0:
            logger.warning(
                MEMORY_FINE_TUNE_INTERRUPTED,
                interrupted_count=count,
            )
        return count


__all__ = ["SQLiteFineTuneRunRepository"]
