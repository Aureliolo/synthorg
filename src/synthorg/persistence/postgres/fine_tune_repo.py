"""Postgres repositories for fine-tuning pipeline runs and checkpoints.

Postgres siblings of ``persistence/sqlite/fine_tune_repo.py``.  The
schema stores JSON-shaped fields as ``JSONB``, timestamps as
``TIMESTAMPTZ``, and ``is_active`` as ``BOOLEAN``; psycopg adapters
handle the wire conversion.  At the Python protocol level both
backends return identical Pydantic models.
"""

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pydantic import ValidationError

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
from synthorg.persistence._shared import normalize_utc
from synthorg.persistence.errors import QueryError

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = get_logger(__name__)

_ACTIVE_STAGES: tuple[str, ...] = tuple(
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


def _clamp_pagination(limit: int, offset: int) -> tuple[int, int]:
    """Clamp list pagination params to valid ranges.

    ``limit`` is clamped to ``[1, _MAX_LIST_LIMIT]`` and ``offset`` to
    ``[0, +inf)``.  Used by both list_runs and list_checkpoints.
    """
    return min(max(limit, 1), _MAX_LIST_LIMIT), max(offset, 0)


def _run_from_row(row: dict[str, Any]) -> FineTuneRun:
    """Build a ``FineTuneRun`` from a JSONB-aware Postgres row.

    Postgres returns JSONB as native Python dict/list and TIMESTAMPTZ
    as tz-aware ``datetime``, so we pass values through without
    parsing.

    Raises:
        QueryError: If the row contains invalid data.
    """
    try:
        return FineTuneRun(
            id=row["id"],
            stage=FineTuneStage(row["stage"]),
            progress=row["progress"],
            error=row["error"],
            config=FineTuneRunConfig.model_validate(row["config_json"]),
            started_at=row["started_at"],
            updated_at=row["updated_at"],
            completed_at=row["completed_at"],
            stages_completed=tuple(row["stages_completed"] or ()),
        )
    except (ValidationError, ValueError, TypeError) as exc:
        msg = f"Corrupt fine-tune run row: {exc}"
        raise QueryError(msg) from exc


def _checkpoint_from_row(row: dict[str, Any]) -> CheckpointRecord:
    """Build a ``CheckpointRecord`` from a JSONB-aware Postgres row.

    ``backup_config_json`` is a JSONB column on the wire but a string
    field on the model, so re-serialise the JSONB value to a JSON
    string before validation to keep the round-trip lossless.  psycopg's
    default JSONB loader returns the *decoded* Python value (dict, list,
    ``str``, ``int``, ...), so a JSONB payload of ``"foo"`` comes back
    as the Python ``str`` ``foo`` -- which is not a valid JSON text.
    Always re-serialise via ``json.dumps`` so the model-side invariant
    (``backup_config_json`` is a JSON text, not an arbitrary Python
    object's ``str``) holds regardless of the JSONB value's type.

    Raises:
        QueryError: If the row contains invalid data.
    """
    try:
        eval_metrics = None
        eval_payload = row["eval_metrics_json"]
        if eval_payload is not None:
            eval_metrics = EvalMetrics.model_validate(eval_payload)

        backup_payload = row["backup_config_json"]
        backup_str: str | None = None
        if backup_payload is not None:
            backup_str = json.dumps(backup_payload)

        return CheckpointRecord(
            id=row["id"],
            run_id=row["run_id"],
            model_path=row["model_path"],
            base_model=row["base_model"],
            doc_count=row["doc_count"],
            eval_metrics=eval_metrics,
            size_bytes=row["size_bytes"],
            created_at=row["created_at"],
            is_active=row["is_active"],
            backup_config_json=backup_str,
        )
    except (ValidationError, ValueError, TypeError) as exc:
        msg = f"Corrupt checkpoint row: {exc}"
        raise QueryError(msg) from exc


class PostgresFineTuneRunRepository:
    """Postgres-backed fine-tuning run repository.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save_run(self, run: FineTuneRun) -> None:
        """Persist a run (upsert semantics)."""
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """\
INSERT INTO fine_tune_runs (
    id, stage, progress, error, config_json,
    started_at, updated_at, completed_at, stages_completed
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s
)
ON CONFLICT (id) DO UPDATE SET
    stage = EXCLUDED.stage,
    progress = EXCLUDED.progress,
    error = EXCLUDED.error,
    config_json = EXCLUDED.config_json,
    updated_at = EXCLUDED.updated_at,
    completed_at = EXCLUDED.completed_at,
    stages_completed = EXCLUDED.stages_completed""",
                    (
                        run.id,
                        run.stage.value,
                        run.progress,
                        run.error,
                        Jsonb(run.config.model_dump(mode="json")),
                        normalize_utc(run.started_at),
                        normalize_utc(run.updated_at),
                        (
                            None
                            if run.completed_at is None
                            else normalize_utc(run.completed_at)
                        ),
                        Jsonb(list(run.stages_completed)),
                    ),
                )
                await conn.commit()
        except psycopg.Error as exc:
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
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT * FROM fine_tune_runs WHERE id = %s",
                    (run_id,),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
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
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT * FROM fine_tune_runs "
                    "WHERE stage = ANY(%s) "
                    "ORDER BY started_at DESC LIMIT 1",
                    (list(_ACTIVE_STAGES),),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
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
        limit, offset = _clamp_pagination(limit, offset)
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute("SELECT COUNT(*) AS n FROM fine_tune_runs")
                count_row = await cur.fetchone()
                total = int(count_row["n"]) if count_row else 0

                await cur.execute(
                    "SELECT * FROM fine_tune_runs "
                    "ORDER BY started_at DESC LIMIT %s OFFSET %s",
                    (limit, offset),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
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
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """\
UPDATE fine_tune_runs SET
    stage = %s,
    progress = %s,
    error = %s,
    config_json = %s,
    updated_at = %s,
    completed_at = %s,
    stages_completed = %s
WHERE id = %s""",
                    (
                        run.stage.value,
                        run.progress,
                        run.error,
                        Jsonb(run.config.model_dump(mode="json")),
                        normalize_utc(run.updated_at),
                        (
                            None
                            if run.completed_at is None
                            else normalize_utc(run.completed_at)
                        ),
                        Jsonb(list(run.stages_completed)),
                        run.id,
                    ),
                )
                await conn.commit()
        except psycopg.Error as exc:
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
        now = datetime.now(UTC)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "UPDATE fine_tune_runs SET "
                    "stage = %s, error = %s, updated_at = %s, completed_at = %s "
                    "WHERE stage = ANY(%s)",
                    (
                        FineTuneStage.FAILED.value,
                        "interrupted by restart",
                        now,
                        now,
                        list(_ACTIVE_STAGES),
                    ),
                )
                count = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
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


class PostgresFineTuneCheckpointRepository:
    """Postgres-backed fine-tuning checkpoint repository.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save_checkpoint(
        self,
        checkpoint: CheckpointRecord,
    ) -> None:
        """Persist a checkpoint (upsert semantics)."""
        eval_payload: Jsonb | None = None
        if checkpoint.eval_metrics is not None:
            eval_payload = Jsonb(
                checkpoint.eval_metrics.model_dump(mode="json"),
            )
        backup_payload = self._encode_backup_config(
            checkpoint.backup_config_json,
            checkpoint_id=checkpoint.id,
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """\
INSERT INTO fine_tune_checkpoints (
    id, run_id, model_path, base_model, doc_count,
    eval_metrics_json, size_bytes, created_at,
    is_active, backup_config_json
) VALUES (
    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
)
ON CONFLICT (id) DO UPDATE SET
    model_path = EXCLUDED.model_path,
    base_model = EXCLUDED.base_model,
    doc_count = EXCLUDED.doc_count,
    eval_metrics_json = EXCLUDED.eval_metrics_json,
    size_bytes = EXCLUDED.size_bytes,
    is_active = EXCLUDED.is_active,
    backup_config_json = EXCLUDED.backup_config_json""",
                    (
                        checkpoint.id,
                        checkpoint.run_id,
                        checkpoint.model_path,
                        checkpoint.base_model,
                        checkpoint.doc_count,
                        eval_payload,
                        checkpoint.size_bytes,
                        normalize_utc(checkpoint.created_at),
                        checkpoint.is_active,
                        backup_payload,
                    ),
                )
                await conn.commit()
        except psycopg.Error as exc:
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
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT * FROM fine_tune_checkpoints WHERE id = %s",
                    (checkpoint_id,),
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
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
        limit, offset = _clamp_pagination(limit, offset)
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT COUNT(*) AS n FROM fine_tune_checkpoints",
                )
                count_row = await cur.fetchone()
                total = int(count_row["n"]) if count_row else 0

                await cur.execute(
                    "SELECT * FROM fine_tune_checkpoints "
                    "ORDER BY created_at DESC LIMIT %s OFFSET %s",
                    (limit, offset),
                )
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to list checkpoints"
            logger.warning(
                MEMORY_FINE_TUNE_PERSIST_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(_checkpoint_from_row(r) for r in rows), total

    async def set_active(self, checkpoint_id: str) -> None:
        """Deactivate all checkpoints and activate *checkpoint_id* atomically.

        The schema enforces ``UNIQUE INDEX (is_active) WHERE is_active = TRUE``
        on Postgres, so the deactivate-then-activate order is mandatory --
        flipping it would violate the partial unique index.

        Raises:
            QueryError: If *checkpoint_id* does not exist or DB fails.
        """
        try:
            async with (
                self._pool.connection() as conn,
                conn.transaction(),
                conn.cursor() as cur,
            ):
                await cur.execute(
                    "UPDATE fine_tune_checkpoints SET is_active = FALSE "
                    "WHERE is_active = TRUE",
                )
                await cur.execute(
                    "UPDATE fine_tune_checkpoints SET is_active = TRUE WHERE id = %s",
                    (checkpoint_id,),
                )
                if cur.rowcount == 0:
                    msg = f"Checkpoint {checkpoint_id} not found"
                    logger.warning(
                        MEMORY_FINE_TUNE_PERSIST_FAILED,
                        checkpoint_id=checkpoint_id,
                        error=msg,
                    )
                    raise QueryError(msg)
        except psycopg.Error as exc:
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

        Scoped to ``WHERE is_active = TRUE`` so the statement uses the
        partial unique index (``idx_ftc_single_active WHERE is_active =
        TRUE``) instead of a sequential scan + no-op rewrite of every
        already-inactive row.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "UPDATE fine_tune_checkpoints SET is_active = FALSE "
                    "WHERE is_active = TRUE",
                )
                await conn.commit()
        except psycopg.Error as exc:
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

        Mirrors SQLite semantics: missing rows are silent no-ops; the
        active row may not be deleted.  The DELETE includes the
        ``is_active = FALSE`` predicate so a row that flipped to active
        between the SELECT and the DELETE is still rejected.
        """
        try:
            async with (
                self._pool.connection() as conn,
                conn.transaction(),
                conn.cursor() as cur,
            ):
                await cur.execute(
                    "SELECT is_active FROM fine_tune_checkpoints WHERE id = %s",
                    (checkpoint_id,),
                )
                row = await cur.fetchone()
                if row is None:
                    return
                if bool(row[0]):
                    msg = f"Cannot delete active checkpoint {checkpoint_id}"
                    logger.warning(
                        MEMORY_FINE_TUNE_PERSIST_FAILED,
                        checkpoint_id=checkpoint_id,
                        error=msg,
                    )
                    raise QueryError(msg)
                await cur.execute(
                    "DELETE FROM fine_tune_checkpoints "
                    "WHERE id = %s AND is_active = FALSE",
                    (checkpoint_id,),
                )
                if cur.rowcount == 0:
                    msg = f"Cannot delete active checkpoint {checkpoint_id}"
                    logger.warning(
                        MEMORY_FINE_TUNE_PERSIST_FAILED,
                        checkpoint_id=checkpoint_id,
                        error="checkpoint became active during delete",
                    )
                    raise QueryError(msg)
        except psycopg.Error as exc:
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
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(
                    "SELECT * FROM fine_tune_checkpoints "
                    "WHERE is_active = TRUE LIMIT 1",
                )
                row = await cur.fetchone()
        except psycopg.Error as exc:
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

    @staticmethod
    def _encode_backup_config(
        payload: str | None,
        *,
        checkpoint_id: str,
    ) -> Jsonb | None:
        """Convert the model-side JSON string to a JSONB-ready value.

        ``backup_config_json`` is ``str | None`` on the model so callers
        can hand the field around opaquely.  The Postgres column is
        JSONB, so the string must be parsed before insertion;
        invalid JSON surfaces as ``QueryError`` rather than a wire-level
        failure that would only show up on commit.
        """
        if payload is None:
            return None
        try:
            return Jsonb(json.loads(payload))
        except json.JSONDecodeError as exc:
            msg = f"backup_config_json for checkpoint {checkpoint_id} is not valid JSON"
            logger.warning(
                MEMORY_FINE_TUNE_PERSIST_FAILED,
                checkpoint_id=checkpoint_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
