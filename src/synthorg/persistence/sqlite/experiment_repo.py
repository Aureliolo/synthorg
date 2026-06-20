# module-kind: repository
"""SQLite repository for the A/B experiment registry.

Durable backing for ``ExperimentRepository``: variant registration
keyed on ``(experiment, variant)`` and subject assignments keyed on
``(experiment, subject_id)``. Assignments are insert-once -- a PK
conflict surfaces as :class:`ConflictError` so the service re-reads the
canonical assignment (stable-bucket guarantee under concurrency).
"""

from datetime import datetime
from typing import NoReturn

import aiosqlite

from synthorg.core.domain_errors import ConflictError
from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.experiments.models import ExperimentAssignment, ExperimentVariant
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.experiments import EXPERIMENT_PERSISTENCE_FAILED
from synthorg.persistence._shared import (
    coerce_row_timestamp,
    format_iso_utc,
    validate_pagination_args,
)
from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)


def _row_to_variant(row: aiosqlite.Row) -> ExperimentVariant:
    return ExperimentVariant(
        experiment=NotBlankStr(str(row["experiment"])),
        variant=NotBlankStr(str(row["variant"])),
        weight=int(row["weight"]),
        description=str(row["description"]),
        created_at=coerce_row_timestamp(row["created_at"]),
    )


def _row_to_assignment(row: aiosqlite.Row) -> ExperimentAssignment:
    return ExperimentAssignment(
        experiment=NotBlankStr(str(row["experiment"])),
        subject_id=NotBlankStr(str(row["subject_id"])),
        variant=NotBlankStr(str(row["variant"])),
        assigned_at=coerce_row_timestamp(row["assigned_at"]),
    )


class SQLiteExperimentRepository:
    """SQLite-backed experiment variant + assignment store.

    Args:
        db: An open aiosqlite connection.
        write_context: Async write-serialising context manager.
    """

    def __init__(
        self, db: aiosqlite.Connection, *, write_context: WriteContext
    ) -> None:
        self._db = db
        self._db.row_factory = aiosqlite.Row
        self._write_context = write_context

    async def save(self, variant: ExperimentVariant) -> None:
        """Upsert a variant keyed on ``(experiment, variant)``.

        Raises:
            QueryError: On database failure.
        """
        sql = """
            INSERT INTO experiment_variants
                (experiment, variant, weight, description, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(experiment, variant) DO UPDATE SET
                weight = excluded.weight,
                description = excluded.description
        """
        params = (
            variant.experiment,
            variant.variant,
            int(variant.weight),
            variant.description,
            format_iso_utc(variant.created_at),
        )
        async with self._write_context():
            try:
                await self._db.execute(sql, params)
                await self._db.commit()
            except (aiosqlite.Error, ValueError) as exc:
                await self._rollback("save")
                self._raise_query_error("save variant", exc)

    async def list_for_experiment(  # lint-allow: list-pagination -- fixed variant set
        self,
        experiment: NotBlankStr,
    ) -> tuple[ExperimentVariant, ...]:
        """Return every variant for ``experiment`` (oldest first).

        Returns:
            Variants ordered by registration timestamp ascending.

        Raises:
            QueryError: On database failure.
        """
        sql = (
            "SELECT experiment, variant, weight, description, created_at "
            "FROM experiment_variants WHERE experiment = ? "
            "ORDER BY created_at ASC, variant ASC"
        )
        try:
            async with self._db.execute(sql, (experiment,)) as cursor:
                rows = await cursor.fetchall()
            return tuple(_row_to_variant(r) for r in rows)
        except (aiosqlite.Error, ValueError) as exc:
            self._raise_query_error("list variants", exc)

    async def delete(self, *, experiment: NotBlankStr, variant: NotBlankStr) -> bool:
        """Delete a variant.

        Returns:
            ``True`` when a row was removed, ``False`` otherwise.

        Raises:
            QueryError: On database failure.
        """
        sql = "DELETE FROM experiment_variants WHERE experiment = ? AND variant = ?"
        async with self._write_context():
            try:
                async with self._db.execute(sql, (experiment, variant)) as cursor:
                    await self._db.commit()
                    return cursor.rowcount > 0
            except (aiosqlite.Error, ValueError) as exc:
                await self._rollback("delete")
                self._raise_query_error("delete variant", exc)

    async def record_assignment(self, assignment: ExperimentAssignment) -> None:
        """Insert the assignment keyed on ``(experiment, subject_id)``.

        Insert-once: a primary-key conflict raises :class:`ConflictError`
        so the caller re-reads the canonical assignment.

        Raises:
            ConflictError: When the subject already has an assignment.
            QueryError: On other database failure.
        """
        sql = """
            INSERT INTO experiment_assignments
                (experiment, subject_id, variant, assigned_at)
            VALUES (?, ?, ?, ?)
        """
        params = (
            assignment.experiment,
            assignment.subject_id,
            assignment.variant,
            format_iso_utc(assignment.assigned_at),
        )
        async with self._write_context():
            try:
                await self._db.execute(sql, params)
                await self._db.commit()
            except aiosqlite.IntegrityError as exc:
                await self._rollback("record_assignment")
                msg = (
                    f"Assignment already exists for subject "
                    f"{assignment.subject_id!r} in experiment "
                    f"{assignment.experiment!r}"
                )
                raise ConflictError(msg) from exc
            except (aiosqlite.Error, ValueError) as exc:
                await self._rollback("record_assignment")
                self._raise_query_error("record assignment", exc)

    async def get_assignment(
        self,
        *,
        experiment: NotBlankStr,
        subject_id: NotBlankStr,
    ) -> ExperimentAssignment | None:
        """Return the recorded assignment, or ``None`` if absent.

        Returns:
            The matching assignment, or ``None``.

        Raises:
            QueryError: On database failure.
        """
        sql = (
            "SELECT experiment, subject_id, variant, assigned_at "
            "FROM experiment_assignments WHERE experiment = ? AND subject_id = ?"
        )
        try:
            async with self._db.execute(sql, (experiment, subject_id)) as cursor:
                row = await cursor.fetchone()
        except (aiosqlite.Error, ValueError) as exc:
            self._raise_query_error("get assignment", exc)
        return None if row is None else _row_to_assignment(row)

    async def list_assignments(
        self,
        experiment: NotBlankStr,
        *,
        limit: int,
        offset: int,
    ) -> tuple[tuple[ExperimentAssignment, ...], int]:
        """Return ``(page, total)`` ordered by ``assigned_at`` descending.

        Returns:
            A ``(page, total)`` tuple: the page of assignments and the
            unbounded total count for the experiment.

        Raises:
            QueryError: On database failure.
        """
        effective_limit = validate_pagination_args(
            limit, offset, event=EXPERIMENT_PERSISTENCE_FAILED
        )
        page_sql = (
            "SELECT experiment, subject_id, variant, assigned_at "
            "FROM experiment_assignments WHERE experiment = ? "
            "ORDER BY assigned_at DESC, subject_id ASC LIMIT ? OFFSET ?"
        )
        count_sql = "SELECT COUNT(*) FROM experiment_assignments WHERE experiment = ?"
        try:
            async with self._db.execute(
                page_sql, (experiment, effective_limit, offset)
            ) as cursor:
                rows = await cursor.fetchall()
            async with self._db.execute(count_sql, (experiment,)) as cursor:
                count_row = await cursor.fetchone()
        except (aiosqlite.Error, ValueError) as exc:
            self._raise_query_error("list assignments", exc)
        total = int(count_row[0]) if count_row is not None else 0
        return tuple(_row_to_assignment(r) for r in rows), total

    async def assigned_at(self, *, now: datetime) -> datetime:
        """Echo ``now`` as the canonical assignment timestamp.

        Returns:
            The supplied ``now`` unchanged.
        """
        return now

    async def _rollback(self, operation: str) -> None:
        try:
            await self._db.rollback()
        except aiosqlite.Error as exc:
            logger.warning(
                EXPERIMENT_PERSISTENCE_FAILED,
                operation=operation,
                phase="rollback",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    def _raise_query_error(self, operation: str, exc: Exception) -> NoReturn:
        logger.warning(
            EXPERIMENT_PERSISTENCE_FAILED,
            operation=operation,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Failed to {operation}: {type(exc).__name__}"
        raise QueryError(msg) from exc


__all__ = ["SQLiteExperimentRepository"]
