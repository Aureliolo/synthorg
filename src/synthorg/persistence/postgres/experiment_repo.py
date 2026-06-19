# module-kind: repository
"""Postgres repository for the A/B experiment registry.

Sibling of :class:`SQLiteExperimentRepository` backed by
``psycopg_pool.AsyncConnectionPool``. Variant registration keyed on
``(experiment, variant)``; assignments are insert-once keyed on
``(experiment, subject_id)`` and surface :class:`ConflictError` on a
primary-key clash so the service re-reads the canonical assignment.
"""

from datetime import datetime
from typing import NoReturn

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

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

logger = get_logger(__name__)


def _row_to_variant(row: DictRow) -> ExperimentVariant:
    return ExperimentVariant(
        experiment=NotBlankStr(str(row["experiment"])),
        variant=NotBlankStr(str(row["variant"])),
        weight=int(row["weight"]),
        description=str(row["description"]),
        created_at=coerce_row_timestamp(row["created_at"]),
    )


def _row_to_assignment(row: DictRow) -> ExperimentAssignment:
    return ExperimentAssignment(
        experiment=NotBlankStr(str(row["experiment"])),
        subject_id=NotBlankStr(str(row["subject_id"])),
        variant=NotBlankStr(str(row["variant"])),
        assigned_at=coerce_row_timestamp(row["assigned_at"]),
    )


class PostgresExperimentRepository:
    """Postgres-backed experiment variant + assignment store.

    Args:
        pool: Async connection pool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def save(self, variant: ExperimentVariant) -> None:
        """Upsert a variant keyed on ``(experiment, variant)``.

        Raises:
            QueryError: On database failure.
        """
        sql = """
            INSERT INTO experiment_variants
                (experiment, variant, weight, description, created_at)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (experiment, variant) DO UPDATE SET
                weight = EXCLUDED.weight,
                description = EXCLUDED.description,
                created_at = EXCLUDED.created_at
        """
        params = (
            variant.experiment,
            variant.variant,
            int(variant.weight),
            variant.description,
            format_iso_utc(variant.created_at),
        )
        try:
            async with self._pool.connection() as conn:
                await conn.execute(sql, params)
                await conn.commit()
        except psycopg.Error as exc:
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
            "FROM experiment_variants WHERE experiment = %s "
            "ORDER BY created_at ASC, variant ASC"
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (experiment,))
                rows = await cur.fetchall()
            return tuple(_row_to_variant(r) for r in rows)
        except psycopg.Error as exc:
            self._raise_query_error("list variants", exc)

    async def delete(self, *, experiment: NotBlankStr, variant: NotBlankStr) -> bool:
        """Delete a variant.

        Returns:
            ``True`` when a row was removed, ``False`` otherwise.

        Raises:
            QueryError: On database failure.
        """
        sql = "DELETE FROM experiment_variants WHERE experiment = %s AND variant = %s"
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, (experiment, variant))
                rowcount = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            self._raise_query_error("delete variant", exc)
        return rowcount > 0

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
            VALUES (%s, %s, %s, %s)
        """
        params = (
            assignment.experiment,
            assignment.subject_id,
            assignment.variant,
            format_iso_utc(assignment.assigned_at),
        )
        try:
            async with self._pool.connection() as conn:
                await conn.execute(sql, params)
                await conn.commit()
        except psycopg.errors.UniqueViolation as exc:
            msg = (
                f"Assignment already exists for subject "
                f"{assignment.subject_id!r} in experiment {assignment.experiment!r}"
            )
            raise ConflictError(msg) from exc
        except psycopg.Error as exc:
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
            "FROM experiment_assignments WHERE experiment = %s AND subject_id = %s"
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (experiment, subject_id))
                row = await cur.fetchone()
        except psycopg.Error as exc:
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
            "FROM experiment_assignments WHERE experiment = %s "
            "ORDER BY assigned_at DESC, subject_id ASC LIMIT %s OFFSET %s"
        )
        count_sql = (
            "SELECT COUNT(*) AS n FROM experiment_assignments WHERE experiment = %s"
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(page_sql, (experiment, effective_limit, offset))
                rows = await cur.fetchall()
                await cur.execute(count_sql, (experiment,))
                count_row = await cur.fetchone()
        except psycopg.Error as exc:
            self._raise_query_error("list assignments", exc)
        total = int(count_row["n"]) if count_row is not None else 0
        return tuple(_row_to_assignment(r) for r in rows), total

    async def assigned_at(self, *, now: datetime) -> datetime:
        """Echo ``now`` as the canonical assignment timestamp.

        Returns:
            The supplied ``now`` unchanged.
        """
        return now

    def _raise_query_error(self, operation: str, exc: Exception) -> NoReturn:
        logger.warning(
            EXPERIMENT_PERSISTENCE_FAILED,
            operation=operation,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Failed to {operation}: {type(exc).__name__}"
        raise QueryError(msg) from exc


__all__ = ["PostgresExperimentRepository"]
