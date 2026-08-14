# module-kind: repository
"""SQLite repository for the durable completion-oracle verdict archive.

Satisfies ``CompletionOracleReportArchiveRepository`` structurally:
append-only writes, newest-first filtered queries, and retention purge. A row
is one review EVENT, carrying its own surrogate key: the gate runs again
whenever a task is decided, re-opened and decided again, and an execution
therefore has as many reports as it had reviews. ``execution_id`` is indexed
rather than unique for that reason, and the archive key closes the newest-first
sort: a re-review is driven by a human decision arriving rather than by a
clock, so two reports can share a timestamp, and every other sort column is one
the pair shares by construction. The full report is
stored as JSON in ``report_json``; ``task_id`` / ``verdict`` /
``reviewer_agent_id`` / ``executor_agent_id`` / ``finding_count`` /
``report_summary`` are structured columns the read surface filters and
previews on without parsing the blob.
"""
# ruff: noqa: S608 -- dynamic WHERE built from hardcoded column names only

import contextlib
import sqlite3
from collections.abc import Mapping
from datetime import datetime

import aiosqlite
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
from synthorg.persistence._shared import (
    format_iso_utc,
    normalize_utc,
    parse_iso_utc,
)
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
from synthorg.persistence.sqlite._shared import (
    WriteContext,
    is_unique_constraint_error,
)

logger = get_logger(__name__)

_COLUMNS = (
    "execution_id, task_id, reviewer_agent_id, executor_agent_id, "
    "reviewer_provider, reviewer_model_id, reviewer_capability, verdict, "
    "finding_count, report_summary, report_json, recorded_at"
)

#: The store assigns ``report_id``, so it is read but never written.
_READ_COLUMNS = f"report_id, {_COLUMNS}"


def _iso(value: datetime) -> object:
    """Render a UTC datetime the way this backend stores ``recorded_at``.

    Args:
        value: The timestamp to bind.

    Returns:
        The ISO-8601 UTC string the TEXT column compares against.
    """
    return format_iso_utc(normalize_utc(value))


_INSERT_SQL = f"""\
INSERT INTO completion_oracle_reports ({_COLUMNS}) VALUES (
    :execution_id, :task_id, :reviewer_agent_id, :executor_agent_id,
    :reviewer_provider, :reviewer_model_id, :reviewer_capability, :verdict,
    :finding_count, :report_summary, :report_json, :recorded_at
)"""


class SQLiteCompletionOracleReportArchiveRepository:
    """SQLite implementation of ``CompletionOracleReportArchiveRepository``.

    Args:
        db: An open aiosqlite connection.
        write_context: Async context manager serialising writes on the shared
            connection.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._write_context = write_context

    async def append(self, record: CompletionOracleReportRecord) -> None:
        """Persist one review event.

        Raises:
            DuplicateRecordError: On a uniqueness violation. A re-reviewed
                execution is an ordinary second row, so no column pair is
                unique and nothing reachable raises this; the translation is
                kept because a future index would surface here.
            QueryError: On other database errors.
        """
        async with self._write_context():
            try:
                await self._db.execute(_INSERT_SQL, self._to_row(record))
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                if is_unique_constraint_error(exc):
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
            placeholder="?",
            empty="1=1",
            serialize_timestamp=_iso,
        )
        sql = (
            f"SELECT {_READ_COLUMNS} FROM completion_oracle_reports WHERE {where} "
            "ORDER BY recorded_at DESC, report_id DESC "
            "LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])
        try:
            async with self._db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to query completion-oracle reports"
            logger.warning(
                COMPLETION_ORACLE_REPORT_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(self._row_to_model(dict(r)) for r in rows)

    async def count(self, filter_spec: CompletionOracleReportFilterSpec) -> int:
        """Return how many records match the filter.

        Returns:
            The matching row count.

        Raises:
            QueryError: If the database query fails.
        """
        where, params = build_completion_oracle_report_filter_clauses(
            filter_spec, placeholder="?", empty="1=1", serialize_timestamp=_iso
        )
        try:
            async with self._db.execute(
                f"SELECT COUNT(*) FROM completion_oracle_reports WHERE {where}",
                params,
            ) as cursor:
                row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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

        Returns:
            Counts keyed by verdict value; a kind with no rows is absent.

        Raises:
            QueryError: If the database query fails.
        """
        where, params = build_completion_oracle_report_filter_clauses(
            filter_spec, placeholder="?", empty="1=1", serialize_timestamp=_iso
        )
        try:
            async with self._db.execute(
                "SELECT verdict, COUNT(*) FROM completion_oracle_reports "
                f"WHERE {where} GROUP BY verdict",
                params,
            ) as cursor:
                rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
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
            Number of rows removed.

        Raises:
            QueryError: If ``threshold`` is naive, or the database query fails.
        """
        if threshold.tzinfo is None:
            msg = "threshold must be timezone-aware; a naive datetime is rejected"
            raise QueryError(msg)
        async with self._write_context():
            try:
                async with self._db.execute(
                    "DELETE FROM completion_oracle_reports WHERE recorded_at < ?",
                    (format_iso_utc(normalize_utc(threshold)),),
                ) as cursor:
                    count = cursor.rowcount
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
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
            serialised into ``report_json`` and an ISO-8601 UTC ``recorded_at``.
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
            "recorded_at": format_iso_utc(normalize_utc(record.recorded_at)),
        }

    def _row_to_model(self, row: dict[str, object]) -> CompletionOracleReportRecord:
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
                recorded_at=parse_iso_utc(str(row["recorded_at"])),
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


__all__ = ["SQLiteCompletionOracleReportArchiveRepository"]
