"""SQLite repositories for fine-tuning pipeline runs and checkpoints."""

import asyncio
import contextlib
import json
import sqlite3
from datetime import UTC, datetime

import aiosqlite

from synthorg.memory.embedding.fine_tune import FineTuneStage
from synthorg.memory.embedding.fine_tune_models import (
    CheckpointRecord,
    EvalMetrics,
    FineTuneRun,
    FineTuneRunConfig,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import (
    MEMORY_FINE_TUNE_INTERRUPTED,
    MEMORY_FINE_TUNE_PERSIST_FAILED,
)
from synthorg.persistence._shared import coerce_row_timestamp, format_iso_utc
from synthorg.persistence.errors import QueryError

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

    Raises:
        QueryError: If the row contains invalid data.
    """
    try:
        config = FineTuneRunConfig.model_validate_json(row["config_json"])
        stages = tuple(json.loads(row["stages_completed"]))
        return FineTuneRun(
            id=row["id"],
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
        msg = f"Corrupt fine-tune run row: {exc}"
        raise QueryError(msg) from exc


def _checkpoint_from_row(row: aiosqlite.Row) -> CheckpointRecord:
    """Build a ``CheckpointRecord`` from a database row.

    Raises:
        QueryError: If the row contains invalid data.
    """
    try:
        eval_metrics = None
        if row["eval_metrics_json"]:
            eval_metrics = EvalMetrics.model_validate_json(
                row["eval_metrics_json"],
            )
        return CheckpointRecord(
            id=row["id"],
            run_id=row["run_id"],
            model_path=row["model_path"],
            base_model=row["base_model"],
            doc_count=row["doc_count"],
            eval_metrics=eval_metrics,
            size_bytes=row["size_bytes"],
            created_at=coerce_row_timestamp(row["created_at"]),
            is_active=bool(row["is_active"]),
            backup_config_json=row["backup_config_json"],
        )
    except (ValueError, TypeError, json.JSONDecodeError) as exc:
        msg = f"Corrupt checkpoint row: {exc}"
        raise QueryError(msg) from exc


class SQLiteFineTuneRunRepository:
    """SQLite-backed fine-tuning run repository.

    Args:
        db: An open aiosqlite connection with row_factory set.
    """

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

    async def save_run(self, run: FineTuneRun) -> None:
        """Persist a run (upsert semantics)."""
        async with self._write_lock:
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
                        run.id,
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
                msg = f"Failed to save fine-tune run {run.id}"
                logger.warning(
                    MEMORY_FINE_TUNE_PERSIST_FAILED,
                    run_id=run.id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def get_run(self, run_id: str) -> FineTuneRun | None:
        """Retrieve a run by ID."""
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
        """Get the currently active run (if any)."""
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

    async def list_runs(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[tuple[FineTuneRun, ...], int]:
        """List runs ordered by start time descending.

        Returns:
            Tuple of (runs, total_count).
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

    async def update_run(self, run: FineTuneRun) -> None:
        """Update all mutable fields for a run."""
        async with self._write_lock:
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
                        run.id,
                    ),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                msg = f"Failed to update fine-tune run {run.id}"
                logger.warning(
                    MEMORY_FINE_TUNE_PERSIST_FAILED,
                    run_id=run.id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def mark_interrupted(self) -> int:
        """Mark all active runs as FAILED on startup recovery.

        Returns:
            Number of runs marked as interrupted.
        """
        placeholders = ", ".join("?" for _ in _ACTIVE_STAGES)
        now = format_iso_utc(datetime.now(UTC))
        query = (
            f"UPDATE fine_tune_runs SET "  # noqa: S608
            f"stage = ?, error = ?, updated_at = ?, completed_at = ? "
            f"WHERE stage IN ({placeholders})"
        )
        async with self._write_lock:
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


class SQLiteFineTuneCheckpointRepository:
    """SQLite-backed fine-tuning checkpoint repository.

    Args:
        db: An open aiosqlite connection with row_factory set.
    """

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

    async def save_checkpoint(
        self,
        checkpoint: CheckpointRecord,
    ) -> None:
        """Persist a checkpoint (upsert semantics)."""
        async with self._write_lock:
            try:
                await self._db.execute(
                    "INSERT INTO fine_tune_checkpoints "
                    "(id, run_id, model_path, base_model, doc_count, "
                    "eval_metrics_json, size_bytes, created_at, "
                    "is_active, backup_config_json) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                    "ON CONFLICT(id) DO UPDATE SET "
                    "model_path = excluded.model_path, "
                    "base_model = excluded.base_model, "
                    "doc_count = excluded.doc_count, "
                    "eval_metrics_json = excluded.eval_metrics_json, "
                    "size_bytes = excluded.size_bytes, "
                    "is_active = excluded.is_active, "
                    "backup_config_json = excluded.backup_config_json",
                    (
                        checkpoint.id,
                        checkpoint.run_id,
                        checkpoint.model_path,
                        checkpoint.base_model,
                        checkpoint.doc_count,
                        checkpoint.eval_metrics.model_dump_json()
                        if checkpoint.eval_metrics
                        else None,
                        checkpoint.size_bytes,
                        format_iso_utc(checkpoint.created_at),
                        int(checkpoint.is_active),
                        checkpoint.backup_config_json,
                    ),
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                msg = f"Failed to save checkpoint {checkpoint.id}"
                logger.warning(
                    MEMORY_FINE_TUNE_PERSIST_FAILED,
                    checkpoint_id=checkpoint.id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def get_checkpoint(
        self,
        checkpoint_id: str,
    ) -> CheckpointRecord | None:
        """Retrieve a checkpoint by ID."""
        try:
            cursor = await self._db.execute(
                "SELECT * FROM fine_tune_checkpoints WHERE id = ?",
                (checkpoint_id,),
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to get checkpoint {checkpoint_id}"
            logger.warning(
                MEMORY_FINE_TUNE_PERSIST_FAILED,
                checkpoint_id=checkpoint_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        return _checkpoint_from_row(row)

    async def list_checkpoints(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[tuple[CheckpointRecord, ...], int]:
        """List checkpoints ordered by creation time descending.

        Returns:
            Tuple of (checkpoints, total_count).
        """
        limit = min(max(limit, 1), _MAX_LIST_LIMIT)
        offset = max(offset, 0)
        try:
            count_cursor = await self._db.execute(
                "SELECT COUNT(*) FROM fine_tune_checkpoints",
            )
            count_row = await count_cursor.fetchone()
            total = count_row[0] if count_row else 0

            cursor = await self._db.execute(
                "SELECT * FROM fine_tune_checkpoints "
                "ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (limit, offset),
            )
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to list checkpoints"
            logger.warning(
                MEMORY_FINE_TUNE_PERSIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(_checkpoint_from_row(r) for r in rows), total

    async def set_active(self, checkpoint_id: str) -> None:
        """Deactivate all checkpoints and activate the given one.

        Uses aiosqlite transaction methods for atomicity.

        Raises:
            QueryError: If the checkpoint does not exist or DB fails.
        """
        async with self._write_lock:
            try:
                await self._db.execute(
                    "UPDATE fine_tune_checkpoints SET is_active = 0",
                )
                cursor = await self._db.execute(
                    "UPDATE fine_tune_checkpoints SET is_active = 1 WHERE id = ?",
                    (checkpoint_id,),
                )
                affected = cursor.rowcount
                if affected == 0:
                    await self._db.rollback()
                    msg = f"Checkpoint {checkpoint_id} not found"
                    logger.warning(
                        MEMORY_FINE_TUNE_PERSIST_FAILED,
                        checkpoint_id=checkpoint_id,
                        error=msg,
                    )
                    raise QueryError(msg)
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(Exception):
                    await self._db.rollback()
                msg = f"Failed to activate checkpoint {checkpoint_id}"
                logger.warning(
                    MEMORY_FINE_TUNE_PERSIST_FAILED,
                    checkpoint_id=checkpoint_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def deactivate_all(self) -> None:
        """Deactivate all checkpoints (for rollback)."""
        async with self._write_lock:
            try:
                await self._db.execute(
                    "UPDATE fine_tune_checkpoints SET is_active = 0",
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                msg = "Failed to deactivate all checkpoints"
                logger.warning(
                    MEMORY_FINE_TUNE_PERSIST_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def delete_checkpoint(
        self,
        checkpoint_id: str,
    ) -> None:
        """Delete a checkpoint. Raises if it is the active one.

        Checks existence and active status before deleting to provide
        clear error messages without TOCTOU races.
        """
        async with self._write_lock:
            try:
                check = await self._db.execute(
                    "SELECT is_active FROM fine_tune_checkpoints WHERE id = ?",
                    (checkpoint_id,),
                )
                row = await check.fetchone()
                if row is None:
                    return
                if bool(row["is_active"]):
                    msg = f"Cannot delete active checkpoint {checkpoint_id}"
                    logger.warning(
                        MEMORY_FINE_TUNE_PERSIST_FAILED,
                        checkpoint_id=checkpoint_id,
                        error=msg,
                    )
                    raise QueryError(msg)
                cursor = await self._db.execute(
                    "DELETE FROM fine_tune_checkpoints WHERE id = ? AND is_active = 0",
                    (checkpoint_id,),
                )
                if cursor.rowcount == 0:
                    # Race: became active between SELECT and DELETE.
                    await self._db.rollback()
                    msg = f"Cannot delete active checkpoint {checkpoint_id}"
                    logger.warning(
                        MEMORY_FINE_TUNE_PERSIST_FAILED,
                        checkpoint_id=checkpoint_id,
                        error="checkpoint became active during delete",
                    )
                    raise QueryError(msg)
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                msg = f"Failed to delete checkpoint {checkpoint_id}"
                logger.warning(
                    MEMORY_FINE_TUNE_PERSIST_FAILED,
                    checkpoint_id=checkpoint_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def get_active_checkpoint(
        self,
    ) -> CheckpointRecord | None:
        """Get the currently active checkpoint (if any)."""
        try:
            cursor = await self._db.execute(
                "SELECT * FROM fine_tune_checkpoints WHERE is_active = 1 LIMIT 1",
            )
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to query active checkpoint"
            logger.warning(
                MEMORY_FINE_TUNE_PERSIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        return _checkpoint_from_row(row)
