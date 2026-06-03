# module-kind: repository
"""Postgres implementation of the ``RedTeamReportArchiveRepository`` protocol.

Postgres sibling of ``persistence/sqlite/red_team_report_repo.py``.
``recorded_at`` is stored as TIMESTAMPTZ; the merged report is stored as
a JSON string in the ``report_json`` TEXT column (parity with the SQLite
backend, where the dual-backend drift gate maps TEXT to TEXT).
"""
# ruff: noqa: S608 -- dynamic WHERE built from hardcoded column names only

from typing import TYPE_CHECKING

import psycopg
from psycopg.rows import DictRow, dict_row
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
from synthorg.persistence._shared import normalize_utc
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.red_team_report_protocol import RedTeamReportFilterSpec
from synthorg.security.redteam.models import (
    RedTeamReport,
    RedTeamReportRecord,
    RedTeamVerdict,
)

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = get_logger(__name__)

_COLUMNS = (
    "execution_id, task_id, verdict, finding_count, "
    "report_summary, report_json, recorded_at"
)

_INSERT_SQL = f"""\
INSERT INTO red_team_reports ({_COLUMNS}) VALUES (
    %(execution_id)s, %(task_id)s, %(verdict)s, %(finding_count)s,
    %(report_summary)s, %(report_json)s, %(recorded_at)s
)"""


class PostgresRedTeamReportArchiveRepository:
    """Postgres implementation of ``RedTeamReportArchiveRepository``.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def append(self, record: RedTeamReportRecord) -> None:
        """Persist one record (append-only; a duplicate execution is a violation).

        Raises:
            DuplicateRecordError: If a record already exists for the same
                ``execution_id``.
            QueryError: On other database errors.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(_INSERT_SQL, self._to_row(record))
                await conn.commit()
        except psycopg.errors.UniqueViolation as exc:
            msg = (
                f"Red-team report for execution {record.execution_id!r} already exists"
            )
            logger.warning(
                RED_TEAM_REPORT_SAVE_FAILED,
                execution_id=record.execution_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise DuplicateRecordError(msg) from exc
        except psycopg.Error as exc:
            msg = (
                f"Failed to save red-team report for execution {record.execution_id!r}"
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
            "ORDER BY recorded_at DESC, execution_id DESC LIMIT %s OFFSET %s"
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
            msg = "Failed to query red-team reports"
            logger.warning(
                RED_TEAM_REPORT_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(self._row_to_model(r) for r in rows)

    async def purge_before(self, threshold: AwareDatetime) -> int:
        """Delete records with ``recorded_at < threshold``.

        ``threshold`` is an ``AwareDatetime`` so naive values cannot slip
        through silently.

        Returns:
            Number of rows deleted.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM red_team_reports WHERE recorded_at < %s",
                    (normalize_utc(threshold),),
                )
                count = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
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
            conditions.append("execution_id = %s")
            params.append(filter_spec.execution_id)
        if filter_spec.task_id is not None:
            conditions.append("task_id = %s")
            params.append(filter_spec.task_id)
        if filter_spec.verdict is not None:
            conditions.append("verdict = %s")
            params.append(filter_spec.verdict.value)
        where = " AND ".join(conditions) if conditions else "TRUE"
        return where, params

    def _to_row(self, record: RedTeamReportRecord) -> dict[str, object]:
        """Flatten a record into a row dict (report JSON-encoded).

        Returns:
            The named-parameter row: structured columns plus the full
            report serialised into ``report_json`` and a UTC-normalised
            ``recorded_at``.
        """
        return {
            "execution_id": record.execution_id,
            "task_id": record.task_id,
            "verdict": record.verdict.value,
            "finding_count": len(record.report.findings),
            "report_summary": record.report.summary,
            "report_json": record.report.model_dump_json(),
            "recorded_at": normalize_utc(record.recorded_at),
        }

    def _row_to_model(self, row: DictRow) -> RedTeamReportRecord:
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
                recorded_at=normalize_utc(row["recorded_at"]),
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


__all__ = ["PostgresRedTeamReportArchiveRepository"]
