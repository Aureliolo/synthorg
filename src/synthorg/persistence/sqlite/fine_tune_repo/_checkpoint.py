"""SQLite repository for fine-tuning checkpoints."""

import contextlib
import json
import sqlite3

import aiosqlite

from synthorg.core.persistence_errors import QueryError
from synthorg.memory.embedding.fine_tune_models import CheckpointRecord, EvalMetrics
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.memory import MEMORY_FINE_TUNE_PERSIST_FAILED
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import coerce_row_timestamp, format_iso_utc
from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)

_MAX_LIST_LIMIT: int = 1_000


def _checkpoint_from_row(row: aiosqlite.Row) -> CheckpointRecord:
    """Build a ``CheckpointRecord`` from a database row.

    Returns:
        Result of type ``CheckpointRecord``.

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
        msg = f"Corrupt checkpoint row: {safe_error_description(exc)}"
        raise QueryError(msg) from exc


class SQLiteFineTuneCheckpointRepository:
    """SQLite-backed fine-tuning checkpoint repository.

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

    async def save(self, entity: CheckpointRecord) -> None:
        """Upsert a checkpoint by id (idempotent semantics).

        Raises:
            QueryError: If the database query fails.
        """
        checkpoint = entity
        async with self._write_context():
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

    async def get(self, entity_id: str) -> CheckpointRecord | None:
        """Retrieve a checkpoint by id.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        checkpoint_id = entity_id
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

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[CheckpointRecord, ...]:
        """List checkpoints in ascending id order (paginated).

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        limit = min(max(limit, 1), _MAX_LIST_LIMIT)
        offset = max(offset, 0)
        try:
            cursor = await self._db.execute(
                "SELECT * FROM fine_tune_checkpoints ORDER BY id ASC LIMIT ? OFFSET ?",
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
        return tuple(_checkpoint_from_row(r) for r in rows)

    async def list_items_page(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[tuple[CheckpointRecord, ...], int]:
        """List checkpoints ordered by creation time descending with total count.

        Returns:
            Tuple of (checkpoints, total_count).

        Raises:
            QueryError: If the database query fails.
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
        async with self._write_context():
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
        """Deactivate all checkpoints (for rollback).

        Raises:
            QueryError: If the database query fails.
        """
        async with self._write_context():
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

    async def delete(self, entity_id: str) -> bool:
        """Delete a checkpoint by id.

        Raises when deleting the active checkpoint (domain invariant).
        Returns ``False`` if checkpoint does not exist.

        Returns:
            ``True`` if deleted, ``False`` if not found.

        Raises:
            QueryError: If the database query fails.
        """
        checkpoint_id = entity_id
        async with self._write_context():
            try:
                check = await self._db.execute(
                    "SELECT is_active FROM fine_tune_checkpoints WHERE id = ?",
                    (checkpoint_id,),
                )
                row = await check.fetchone()
                if row is None:
                    return False
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
            else:
                return True

    async def get_active_checkpoint(
        self,
    ) -> CheckpointRecord | None:
        """Get the currently active checkpoint (if any).

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
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


__all__ = ["SQLiteFineTuneCheckpointRepository"]
