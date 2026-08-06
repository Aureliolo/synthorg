# module-kind: repository
"""SQLite repository implementation for initiative evaluation verdicts."""
# ruff: noqa: S608 -- dynamic WHERE built from hardcoded column names only

import contextlib
import json
import sqlite3
from datetime import datetime

import aiosqlite
from pydantic import ValidationError

from synthorg.core.persistence_errors import DuplicateRecordError, QueryError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.evaluation_report import (
    PERSISTENCE_EVALUATION_REPORT_DELETE_FAILED,
    PERSISTENCE_EVALUATION_REPORT_DESERIALIZE_FAILED,
    PERSISTENCE_EVALUATION_REPORT_QUERY_FAILED,
    PERSISTENCE_EVALUATION_REPORT_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import normalize_utc
from synthorg.persistence._shared._filter_clauses import (
    build_evaluation_report_filter_clauses,
)
from synthorg.persistence._shared.datetime_marshaller import (
    coerce_row_timestamp,
    format_iso_utc,
)
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.evaluation_report_protocol import (
    EvaluationReportFilterSpec,
    EvaluationReportRecord,
)
from synthorg.persistence.sqlite._shared import (
    WriteContext,
    is_unique_constraint_error,
)

logger = get_logger(__name__)

_COLUMNS = (
    "record_id, plan_id, project_id, attempt, verdict_summary, verdicts, "
    "objective_met, evaluated_at"
)

_INSERT_SQL = f"""\
INSERT INTO initiative_evaluation_report ({_COLUMNS}) VALUES (
    :record_id, :plan_id, :project_id, :attempt, :verdict_summary, :verdicts,
    :objective_met, :evaluated_at
)"""


class SQLiteEvaluationReportRepository:
    """SQLite implementation of ``EvaluationReportRepository``.

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

    async def append(self, record: EvaluationReportRecord) -> None:
        """Persist one judgement (append-only).

        Raises:
            DuplicateRecordError: If this plan already has this attempt, or
                a record with the same id exists. Both are the same thing
                to a caller: the judgement it is trying to write is already
                on record, and overwriting it would erase evidence.
            QueryError: If the database query fails.
        """
        async with self._write_context():
            try:
                await self._db.execute(_INSERT_SQL, _to_row(record))
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                logger.warning(
                    PERSISTENCE_EVALUATION_REPORT_SAVE_FAILED,
                    plan_id=record.plan_id,
                    attempt=record.attempt,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                if is_unique_constraint_error(exc):
                    msg = (
                        f"Evaluation report for plan {record.plan_id!r} "
                        f"attempt {record.attempt} already exists"
                    )
                    raise DuplicateRecordError(msg) from exc
                msg = f"Failed to save evaluation report for {record.plan_id!r}"
                raise QueryError(msg) from exc

    async def query(
        self,
        filter_spec: EvaluationReportFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[EvaluationReportRecord, ...]:
        """Return judgements matching the filter, newest-first.

        Returns:
            The matching judgements.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_EVALUATION_REPORT_QUERY_FAILED
        )
        where, params = build_evaluation_report_filter_clauses(
            filter_spec, placeholder="?", empty="1=1"
        )
        sql = (
            f"SELECT {_COLUMNS} FROM initiative_evaluation_report WHERE {where} "
            "ORDER BY evaluated_at DESC, attempt DESC LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])
        try:
            async with self._db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to query evaluation reports"
            logger.warning(
                PERSISTENCE_EVALUATION_REPORT_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(_row_to_model(dict(r)) for r in rows)

    async def purge_before(self, threshold: datetime) -> int:
        """Delete judgements with ``evaluated_at < threshold``.

        Args:
            threshold: Timezone-aware UTC timestamp. A naive datetime is
                rejected to prevent silent local-time misinterpretation
                deleting the wrong retention window.

        Returns:
            Number of rows deleted.

        Raises:
            QueryError: If *threshold* is naive or the database query fails.
        """
        if threshold.tzinfo is None:
            msg = "threshold must be timezone-aware; a naive datetime is rejected"
            raise QueryError(msg)
        threshold = normalize_utc(threshold)
        async with self._write_context():
            try:
                async with self._db.execute(
                    "DELETE FROM initiative_evaluation_report WHERE evaluated_at < ?",
                    (format_iso_utc(threshold),),
                ) as cursor:
                    count = cursor.rowcount
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = "Failed to purge evaluation reports by threshold"
                logger.warning(
                    PERSISTENCE_EVALUATION_REPORT_DELETE_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return count

    async def max_attempt(self, plan_id: NotBlankStr) -> int:
        """Return the highest attempt recorded for *plan_id*, or 0 if none.

        Returns:
            The maximum ``attempt`` across the plan's rows.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._db.execute(
                "SELECT MAX(attempt) AS m FROM initiative_evaluation_report"
                " WHERE plan_id = ?",
                (plan_id,),
            ) as cursor:
                row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to read the evaluation attempt ceiling"
            logger.warning(
                PERSISTENCE_EVALUATION_REPORT_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        highest = row["m"] if row is not None else None
        return int(highest) if highest is not None else 0


def _to_row(record: EvaluationReportRecord) -> dict[str, object]:
    """Flatten a judgement into a row dict.

    Returns:
        The row, with the verdicts as a JSON blob.
    """
    return {
        "record_id": str(record.record_id),
        "plan_id": record.plan_id,
        "project_id": record.project_id,
        "attempt": record.attempt,
        "verdict_summary": record.summary,
        "verdicts": json.dumps(
            [v.model_dump(mode="json") for v in record.verdicts],
        ),
        "objective_met": int(record.objective_met),
        "evaluated_at": format_iso_utc(normalize_utc(record.evaluated_at)),
    }


def _row_to_model(row: dict[str, object]) -> EvaluationReportRecord:
    """Convert a database row to an ``EvaluationReportRecord``.

    Returns:
        The deserialised judgement.

    Raises:
        QueryError: If the row cannot be deserialized.
    """
    try:
        return EvaluationReportRecord.model_validate(
            {
                "record_id": row["record_id"],
                "plan_id": row["plan_id"],
                "project_id": row["project_id"],
                "attempt": row["attempt"],
                "summary": row["verdict_summary"],
                "verdicts": json.loads(str(row.get("verdicts") or "[]")),
                "objective_met": bool(row.get("objective_met")),
                # Shared with the Postgres sibling so both backends put the
                # same offset on the wire whatever the driver hands back.
                "evaluated_at": coerce_row_timestamp(row["evaluated_at"]),
            }
        )
    except (ValidationError, json.JSONDecodeError) as exc:
        msg = f"Failed to deserialize evaluation report {row.get('record_id')!r}"
        logger.warning(
            PERSISTENCE_EVALUATION_REPORT_DESERIALIZE_FAILED,
            record_id=row.get("record_id"),
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc
