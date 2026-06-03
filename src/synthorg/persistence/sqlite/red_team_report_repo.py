"""SQLite repository for the durable red-team report archive.

Satisfies ``RedTeamReportArchiveRepository`` structurally: append-only
writes keyed by ``execution_id`` (single-shot via the primary key),
newest-first filtered queries, and retention purge. The full merged
report is stored as JSON in ``report_json``; ``task_id`` / ``verdict`` /
``finding_count`` / ``report_summary`` are structured columns the read
surface filters and previews on without parsing the blob.
"""
# ruff: noqa: S608 -- dynamic WHERE built from hardcoded column names only

import contextlib
import sqlite3

import aiosqlite
from pydantic import AwareDatetime, ValidationError

from synthorg.core.persistence_errors import DuplicateRecordError, QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.red_team import (
    RED_TEAM_REPORT_DELETE_FAILED,
    RED_TEAM_REPORT_DESERIALIZE_FAILED,
    RED_TEAM_REPORT_QUERY_FAILED,
    RED_TEAM_REPORT_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import normalize_utc, parse_iso_utc
from synthorg.persistence._shared.datetime_marshaller import format_iso_utc
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.red_team_report_protocol import RedTeamReportFilterSpec
from synthorg.persistence.sqlite._shared import (
    WriteContext,
    is_unique_constraint_error,
)
from synthorg.security.redteam.models import (
    RedTeamReport,
    RedTeamReportRecord,
    RedTeamVerdict,
)

logger = get_logger(__name__)

_COLUMNS = (
    "execution_id, task_id, verdict, finding_count, "
    "report_summary, report_json, recorded_at"
)

_INSERT_SQL = f"""\
INSERT INTO red_team_reports ({_COLUMNS}) VALUES (
    :execution_id, :task_id, :verdict, :finding_count, :report_summary,
    :report_json, :recorded_at
)"""


class SQLiteRedTeamReportArchiveRepository:
    """SQLite implementation of ``RedTeamReportArchiveRepository``.

    Args:
        db: An open aiosqlite connection.
        write_context: Async context manager that serialises writes on
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

    async def append(self, record: RedTeamReportRecord) -> None:
        """Persist one record (append-only; a duplicate execution is a violation).

        Raises:
            DuplicateRecordError: If a record already exists for the same
                ``execution_id``.
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
                        "Red-team report for execution "
                        f"{record.execution_id!r} already exists"
                    )
                    logger.warning(
                        RED_TEAM_REPORT_SAVE_FAILED,
                        execution_id=record.execution_id,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )
                    raise DuplicateRecordError(msg) from exc
                msg = (
                    "Failed to save red-team report for execution "
                    f"{record.execution_id!r}"
                )
                logger.warning(
                    RED_TEAM_REPORT_SAVE_FAILED,
                    execution_id=record.execution_id,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc

    async def query(
        self,
        filter_spec: RedTeamReportFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[RedTeamReportRecord, ...]:
        """Return records matching the filter, newest-first by ``recorded_at``.

        Returns:
            The matching records.

        Raises:
            QueryError: If the database query fails.
        """
        limit = validate_pagination_args(
            limit, offset, event=RED_TEAM_REPORT_QUERY_FAILED
        )
        where, params = self._build_where(filter_spec)
        sql = (
            f"SELECT {_COLUMNS} FROM red_team_reports WHERE {where} "
            "ORDER BY recorded_at DESC, execution_id DESC LIMIT ? OFFSET ?"
        )
        params.extend([limit, offset])
        try:
            cursor = await self._db.execute(sql, params)
            rows = await cursor.fetchall()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to query red-team reports"
            logger.warning(
                RED_TEAM_REPORT_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(self._row_to_model(dict(r)) for r in rows)

    async def purge_before(self, threshold: AwareDatetime) -> int:
        """Delete records with ``recorded_at < threshold``.

        ``threshold`` is an ``AwareDatetime`` so naive values cannot slip
        through silently.

        Returns:
            Number of rows removed.

        Raises:
            QueryError: If the database query fails.
        """
        async with self._write_context():
            try:
                cursor = await self._db.execute(
                    "DELETE FROM red_team_reports WHERE recorded_at < ?",
                    (format_iso_utc(normalize_utc(threshold)),),
                )
                count = cursor.rowcount
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                with contextlib.suppress(sqlite3.Error, aiosqlite.Error):
                    await self._db.rollback()
                msg = "Failed to purge red-team reports by threshold"
                logger.warning(
                    RED_TEAM_REPORT_DELETE_FAILED,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return count

    def _build_where(
        self, filter_spec: RedTeamReportFilterSpec
    ) -> tuple[str, list[object]]:
        """Build the WHERE clause + positional params for ``filter_spec``.

        Returns:
            ``(where_clause, params)`` where ``where_clause`` is the SQL
            fragment (without the leading ``WHERE``) and ``params`` is the
            matching positional parameter list.
        """
        conditions: list[str] = []
        params: list[object] = []
        if filter_spec.execution_id is not None:
            conditions.append("execution_id = ?")
            params.append(filter_spec.execution_id)
        if filter_spec.task_id is not None:
            conditions.append("task_id = ?")
            params.append(filter_spec.task_id)
        if filter_spec.verdict is not None:
            conditions.append("verdict = ?")
            params.append(filter_spec.verdict.value)
        where = " AND ".join(conditions) if conditions else "1=1"
        return where, params

    def _to_row(self, record: RedTeamReportRecord) -> dict[str, object]:
        """Flatten a record into a row dict (report JSON-encoded).

        Returns:
            The named-parameter row: structured columns plus the full
            report serialised into ``report_json`` and an ISO-8601 UTC
            ``recorded_at``.
        """
        return {
            "execution_id": record.execution_id,
            "task_id": record.task_id,
            "verdict": record.verdict.value,
            "finding_count": len(record.report.findings),
            "report_summary": record.report.summary,
            "report_json": record.report.model_dump_json(),
            "recorded_at": format_iso_utc(normalize_utc(record.recorded_at)),
        }

    def _row_to_model(self, row: dict[str, object]) -> RedTeamReportRecord:
        """Convert a database row to a ``RedTeamReportRecord`` model.

        Raises:
            QueryError: If the row cannot be deserialized.

        Returns:
            The record reconstructed from the row, with the report
            decoded from ``report_json``.
        """
        try:
            report = RedTeamReport.model_validate_json(str(row["report_json"]))
            return RedTeamReportRecord(
                execution_id=str(row["execution_id"]),
                task_id=str(row["task_id"]),
                verdict=RedTeamVerdict(str(row["verdict"])),
                report=report,
                recorded_at=parse_iso_utc(str(row["recorded_at"])),
            )
        except (ValidationError, ValueError, KeyError) as exc:
            msg = (
                "Failed to deserialize red-team report for execution "
                f"{row.get('execution_id')!r}"
            )
            logger.warning(
                RED_TEAM_REPORT_DESERIALIZE_FAILED,
                execution_id=row.get("execution_id"),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc


__all__ = ["SQLiteRedTeamReportArchiveRepository"]
