# module-kind: repository
"""Postgres implementation of ``CompletionOracleReportArchiveRepository``.

Postgres sibling of ``persistence/sqlite/completion_oracle_report_repo.py``.
``recorded_at`` is stored as TIMESTAMPTZ; the report is stored as a JSON
string in the ``report_json`` TEXT column (parity with the SQLite backend).
The archive key closes the newest-first sort on both backends alike: a
re-review is driven by a human decision arriving rather than by a clock, so
two reports of one execution can share a timestamp, and every other sort
column is one the pair shares by construction.
"""
# ruff: noqa: S608 -- dynamic WHERE built from hardcoded column names only

from collections.abc import Mapping
from datetime import datetime

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import ValidationError

from synthorg.core.persistence_errors import DuplicateRecordError, QueryError
from synthorg.engine.completion_oracle.review_models import (
    CompletionOracleReport,
    CompletionOracleReportRecord,
    CompletionOracleVerdict,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.completion_oracle import (
    COMPLETION_ORACLE_REPORT_DELETE_FAILED,
    COMPLETION_ORACLE_REPORT_DESERIALIZE_FAILED,
    COMPLETION_ORACLE_REPORT_QUERY_FAILED,
    COMPLETION_ORACLE_REPORT_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import normalize_utc
from synthorg.persistence._shared._filter_clauses import (
    build_completion_oracle_report_filter_clauses,
)
from synthorg.persistence._shared._gate_verdict_columns import (
    archive_key,
    optional_capability,
    optional_text,
)
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.completion_oracle_report_protocol import (
    CompletionOracleReportFilterSpec,
)

logger = get_logger(__name__)

_COLUMNS = (
    "execution_id, task_id, reviewer_agent_id, executor_agent_id, "
    "reviewer_provider, reviewer_model_id, reviewer_capability, verdict, "
    "finding_count, report_summary, report_json, recorded_at"
)

#: The store assigns ``report_id``, so it is read but never written.
_READ_COLUMNS = f"report_id, {_COLUMNS}"

_INSERT_SQL = f"""\
INSERT INTO completion_oracle_reports ({_COLUMNS}) VALUES (
    %(execution_id)s, %(task_id)s, %(reviewer_agent_id)s, %(executor_agent_id)s,
    %(reviewer_provider)s, %(reviewer_model_id)s, %(reviewer_capability)s,
    %(verdict)s, %(finding_count)s, %(report_summary)s, %(report_json)s,
    %(recorded_at)s
)"""


class PostgresCompletionOracleReportArchiveRepository:
    """Postgres implementation of ``CompletionOracleReportArchiveRepository``.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def append(self, record: CompletionOracleReportRecord) -> None:
        """Persist one review event.

        Raises:
            DuplicateRecordError: On a uniqueness violation. A re-reviewed
                execution is an ordinary second row, so no column pair is
                unique and nothing reachable raises this; the translation is
                kept because a future index would surface here.
            QueryError: On other database errors.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(_INSERT_SQL, self._to_row(record))
                await conn.commit()
        except psycopg.errors.UniqueViolation as exc:
            msg = (
                "Completion-oracle report for execution "
                f"{record.execution_id!r} already exists"
            )
            logger.warning(
                COMPLETION_ORACLE_REPORT_SAVE_FAILED,
                execution_id=record.execution_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise DuplicateRecordError(msg) from exc
        except psycopg.Error as exc:
            msg = (
                "Failed to save completion-oracle report for execution "
                f"{record.execution_id!r}"
            )
            logger.warning(
                COMPLETION_ORACLE_REPORT_SAVE_FAILED,
                execution_id=record.execution_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def query(
        self,
        filter_spec: CompletionOracleReportFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[CompletionOracleReportRecord, ...]:
        """Return records matching the filter, newest-first by ``recorded_at``.

        Returns:
            The matching records.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=COMPLETION_ORACLE_REPORT_QUERY_FAILED
        )
        where, params = build_completion_oracle_report_filter_clauses(
            filter_spec,
            placeholder="%s",
            empty="TRUE",
            serialize_timestamp=normalize_utc,
        )
        sql = (
            f"SELECT {_READ_COLUMNS} FROM completion_oracle_reports WHERE {where} "
            "ORDER BY recorded_at DESC, report_id DESC "
            "LIMIT %s OFFSET %s"
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
            msg = "Failed to query completion-oracle reports"
            logger.warning(
                COMPLETION_ORACLE_REPORT_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(self._row_to_model(r) for r in rows)

    async def count(self, filter_spec: CompletionOracleReportFilterSpec) -> int:
        """Return how many records match the filter.

        The total is over the whole filter, so any keyset cursor on the spec
        is ignored: a caller reusing one spec for the page and the total
        would otherwise watch the total shrink with every page it fetched.

        Returns:
            The matching row count, independent of paging position.

        Raises:
            QueryError: If the database query fails.
        """
        where, params = build_completion_oracle_report_filter_clauses(
            filter_spec,
            placeholder="%s",
            empty="TRUE",
            serialize_timestamp=normalize_utc,
            keyset=False,
        )
        sql = f"SELECT COUNT(*) FROM completion_oracle_reports WHERE {where}"
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, params)
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = "Failed to count completion-oracle reports"
            logger.warning(
                COMPLETION_ORACLE_REPORT_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return int(row[0]) if row is not None else 0

    async def count_by_verdict(
        self, filter_spec: CompletionOracleReportFilterSpec
    ) -> Mapping[str, int]:
        """Return the matching row count for every verdict kind present.

        Totals are over the whole filter, so any keyset cursor on the spec is
        ignored, as in :meth:`count`.

        Returns:
            Counts keyed by verdict value, independent of paging position; a
            kind with no rows is absent.

        Raises:
            QueryError: If the database query fails.
        """
        where, params = build_completion_oracle_report_filter_clauses(
            filter_spec,
            placeholder="%s",
            empty="TRUE",
            serialize_timestamp=normalize_utc,
            keyset=False,
        )
        sql = (
            "SELECT verdict, COUNT(*) FROM completion_oracle_reports "
            f"WHERE {where} GROUP BY verdict"
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, params)
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to count completion-oracle reports by verdict"
            logger.warning(
                COMPLETION_ORACLE_REPORT_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return {str(row[0]): int(row[1]) for row in rows}

    async def purge_before(self, threshold: datetime) -> int:
        """Delete records with ``recorded_at < threshold``.

        Returns:
            Number of rows deleted.

        Raises:
            QueryError: If ``threshold`` is naive, or the database query fails.
        """
        if threshold.tzinfo is None:
            msg = "threshold must be timezone-aware; a naive datetime is rejected"
            raise QueryError(msg)
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM completion_oracle_reports WHERE recorded_at < %s",
                    (normalize_utc(threshold),),
                )
                count = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            msg = "Failed to purge completion-oracle reports by threshold"
            logger.warning(
                COMPLETION_ORACLE_REPORT_DELETE_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return count

    def _to_row(self, record: CompletionOracleReportRecord) -> dict[str, object]:
        """Flatten a record into a row dict (report JSON-encoded).

        Returns:
            The named-parameter row: structured columns plus the full report
            serialised into ``report_json`` and a UTC-normalised ``recorded_at``.
        """
        return {
            "execution_id": record.execution_id,
            "task_id": record.task_id,
            # From the record, not the embedded report: the gate stamps who
            # it selected, while the report is written by the thing under
            # scrutiny and is not evidence of who reviewed it.
            "reviewer_agent_id": record.reviewer_agent_id,
            "executor_agent_id": record.executor_agent_id,
            "reviewer_provider": record.reviewer_provider,
            "reviewer_model_id": record.reviewer_model_id,
            "reviewer_capability": record.reviewer_capability,
            "verdict": record.verdict.value,
            "finding_count": len(record.report.findings),
            "report_summary": record.report.summary,
            "report_json": record.report.model_dump_json(),
            "recorded_at": normalize_utc(record.recorded_at),
        }

    def _row_to_model(self, row: DictRow) -> CompletionOracleReportRecord:
        """Convert a database row to a ``CompletionOracleReportRecord`` model.

        Returns:
            The record reconstructed from the row, with the report decoded
            from ``report_json``.

        Raises:
            QueryError: If the row cannot be deserialized.
        """
        try:
            report = CompletionOracleReport.model_validate_json(str(row["report_json"]))
            return CompletionOracleReportRecord(
                report_id=archive_key(row["report_id"]),
                execution_id=str(row["execution_id"]),
                task_id=str(row["task_id"]),
                verdict=CompletionOracleVerdict(str(row["verdict"])),
                report=report,
                recorded_at=normalize_utc(row["recorded_at"]),
                reviewer_agent_id=optional_text(row["reviewer_agent_id"]),
                executor_agent_id=optional_text(row["executor_agent_id"]),
                reviewer_provider=optional_text(row["reviewer_provider"]),
                reviewer_model_id=optional_text(row["reviewer_model_id"]),
                reviewer_capability=optional_capability(row["reviewer_capability"]),
            )
        except (ValidationError, ValueError, KeyError, IndexError, TypeError) as exc:
            msg = (
                "Failed to deserialize completion-oracle report for execution "
                f"{row.get('execution_id')!r}"
            )
            logger.warning(
                COMPLETION_ORACLE_REPORT_DESERIALIZE_FAILED,
                execution_id=row.get("execution_id"),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc


__all__ = ["PostgresCompletionOracleReportArchiveRepository"]
