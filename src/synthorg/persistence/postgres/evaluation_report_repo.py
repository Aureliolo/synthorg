# module-kind: repository
"""Postgres implementation of the ``EvaluationReportRepository`` protocol.

Postgres sibling of ``persistence/sqlite/evaluation_report_repo.py``.
``evaluated_at`` is stored as TIMESTAMPTZ, ``objective_met`` as BOOLEAN,
and the per-criterion verdicts as JSONB.
"""
# ruff: noqa: S608 -- dynamic WHERE built from hardcoded column names only

import json
from datetime import datetime

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import ValidationError

from synthorg.core.persistence_errors import DuplicateRecordError, QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.initiative import (
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
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.evaluation_report_protocol import (
    EvaluationReportFilterSpec,
    EvaluationReportRecord,
)

logger = get_logger(__name__)

_COLUMNS = (
    "record_id, plan_id, project_id, attempt, summary, verdicts, "
    "objective_met, evaluated_at"
)

_INSERT_SQL = f"""\
INSERT INTO initiative_evaluation_report ({_COLUMNS}) VALUES (
    %(record_id)s, %(plan_id)s, %(project_id)s, %(attempt)s, %(summary)s,
    %(verdicts)s, %(objective_met)s, %(evaluated_at)s
)"""


class PostgresEvaluationReportRepository:
    """Postgres implementation of ``EvaluationReportRepository``.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def append(self, record: EvaluationReportRecord) -> None:
        """Persist one judgement (append-only).

        Raises:
            DuplicateRecordError: If this plan already has this attempt.
                Overwriting would erase the evidence the replan points at.
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(_INSERT_SQL, _to_row(record))
                await conn.commit()
        except psycopg.errors.UniqueViolation as exc:
            msg = (
                f"Evaluation report for plan {record.plan_id!r} "
                f"attempt {record.attempt} already exists"
            )
            logger.warning(
                PERSISTENCE_EVALUATION_REPORT_SAVE_FAILED,
                plan_id=record.plan_id,
                attempt=record.attempt,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise DuplicateRecordError(msg) from exc
        except psycopg.Error as exc:
            msg = f"Failed to save evaluation report for {record.plan_id!r}"
            logger.warning(
                PERSISTENCE_EVALUATION_REPORT_SAVE_FAILED,
                plan_id=record.plan_id,
                attempt=record.attempt,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
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
            filter_spec, placeholder="%s", empty="TRUE"
        )
        sql = (
            f"SELECT {_COLUMNS} FROM initiative_evaluation_report WHERE {where} "
            "ORDER BY evaluated_at DESC, attempt DESC LIMIT %s OFFSET %s"
        )
        all_params = [*params, limit, offset]
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, all_params)
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to query evaluation reports"
            logger.warning(
                PERSISTENCE_EVALUATION_REPORT_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(_row_to_model(r) for r in rows)

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
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM initiative_evaluation_report WHERE evaluated_at < %s",
                    (normalize_utc(threshold),),
                )
                count = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            msg = "Failed to purge evaluation reports by threshold"
            logger.warning(
                PERSISTENCE_EVALUATION_REPORT_DELETE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return count


def _to_row(record: EvaluationReportRecord) -> dict[str, object]:
    """Flatten a judgement into a row dict.

    Returns:
        The row, with the verdicts as JSONB.
    """
    return {
        "record_id": str(record.record_id),
        "plan_id": record.plan_id,
        "project_id": record.project_id,
        "attempt": record.attempt,
        "summary": record.summary,
        "verdicts": Jsonb([v.model_dump(mode="json") for v in record.verdicts]),
        "objective_met": record.objective_met,
        "evaluated_at": normalize_utc(record.evaluated_at),
    }


def _row_to_model(row: DictRow) -> EvaluationReportRecord:
    """Convert a database row to an ``EvaluationReportRecord``.

    Returns:
        The deserialised judgement.

    Raises:
        QueryError: If the row cannot be deserialized.
    """
    raw = row.get("verdicts")
    try:
        return EvaluationReportRecord.model_validate(
            {
                **row,
                "verdicts": json.loads(raw) if isinstance(raw, str) else raw,
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
