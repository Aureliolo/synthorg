# module-kind: repository
"""Postgres implementation of the ``RedTeamReportArchiveRepository`` protocol.

Postgres sibling of ``persistence/sqlite/red_team_report_repo.py``.
``recorded_at`` is stored as TIMESTAMPTZ; the merged report is stored as
a JSON string in the ``report_json`` TEXT column (parity with the SQLite
backend, where the dual-backend drift gate maps TEXT to TEXT). The archive
key closes the newest-first sort on both backends alike: a re-attack follows
a task being re-opened rather than a clock, so two reports of one execution
can share a timestamp, and every other sort column is one the pair shares by
construction.
"""
# ruff: noqa: S608 -- dynamic WHERE built from hardcoded column names only

from collections.abc import Mapping
from datetime import datetime

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool
from pydantic import ValidationError

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
from synthorg.persistence._shared._filter_clauses import (
    build_red_team_report_filter_clauses,
)
from synthorg.persistence._shared._gate_verdict_columns import (
    archive_key,
    optional_capability,
    optional_text,
)
from synthorg.persistence._shared.pagination import validate_pagination_args
from synthorg.persistence.red_team_report_protocol import RedTeamReportFilterSpec
from synthorg.security.redteam.models import (
    RedTeamReport,
    RedTeamReportRecord,
    RedTeamVerdict,
)

logger = get_logger(__name__)

_COLUMNS = (
    "execution_id, task_id, red_team_agent_id, executor_agent_id, "
    "red_team_provider, red_team_model_id, red_team_capability, verdict, "
    "finding_count, report_summary, report_json, recorded_at"
)

#: The store assigns ``report_id``, so it is read but never written.
_READ_COLUMNS = f"report_id, {_COLUMNS}"

_INSERT_SQL = f"""\
INSERT INTO red_team_reports ({_COLUMNS}) VALUES (
    %(execution_id)s, %(task_id)s, %(red_team_agent_id)s, %(executor_agent_id)s,
    %(red_team_provider)s, %(red_team_model_id)s, %(red_team_capability)s,
    %(verdict)s, %(finding_count)s, %(report_summary)s, %(report_json)s,
    %(recorded_at)s
)"""


class PostgresRedTeamReportArchiveRepository:
    """Postgres implementation of ``RedTeamReportArchiveRepository``.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def append(self, record: RedTeamReportRecord) -> None:
        """Persist one attack event.

        Raises:
            DuplicateRecordError: On a uniqueness violation. A re-attacked
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
        where, params = build_red_team_report_filter_clauses(
            filter_spec,
            placeholder="%s",
            empty="TRUE",
            serialize_timestamp=normalize_utc,
        )
        sql = (
            f"SELECT {_READ_COLUMNS} FROM red_team_reports WHERE {where} "
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
            msg = "Failed to query red-team reports"
            logger.warning(
                RED_TEAM_REPORT_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return tuple(self._row_to_model(r) for r in rows)

    async def count(self, filter_spec: RedTeamReportFilterSpec) -> int:
        """Return how many records match the filter.

        The total is over the whole filter, so any keyset cursor on the spec
        is ignored: a caller reusing one spec for the page and the total
        would otherwise watch the total shrink with every page it fetched.

        Returns:
            The matching row count, independent of paging position.

        Raises:
            QueryError: If the database query fails.
        """
        where, params = build_red_team_report_filter_clauses(
            filter_spec,
            placeholder="%s",
            empty="TRUE",
            serialize_timestamp=normalize_utc,
            keyset=False,
        )
        sql = f"SELECT COUNT(*) FROM red_team_reports WHERE {where}"
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, params)
                row = await cur.fetchone()
        except psycopg.Error as exc:
            msg = "Failed to count red-team reports"
            logger.warning(
                RED_TEAM_REPORT_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return int(row[0]) if row is not None else 0

    async def count_by_verdict(
        self, filter_spec: RedTeamReportFilterSpec
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
        where, params = build_red_team_report_filter_clauses(
            filter_spec,
            placeholder="%s",
            empty="TRUE",
            serialize_timestamp=normalize_utc,
            keyset=False,
        )
        sql = (
            "SELECT verdict, COUNT(*) FROM red_team_reports "
            f"WHERE {where} GROUP BY verdict"
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, params)
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            msg = "Failed to count red-team reports by verdict"
            logger.warning(
                RED_TEAM_REPORT_QUERY_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return {str(row[0]): int(row[1]) for row in rows}

    async def purge_before(self, threshold: datetime) -> int:
        """Delete records with ``recorded_at < threshold``.

        ``threshold`` must be timezone-aware; a naive value is rejected by an
        explicit guard (``normalize_utc`` coerces naive datetimes to UTC rather
        than raising, so it cannot enforce this on its own). The annotation is a
        plain ``datetime`` (matching the sibling repositories) so runtime
        type-checking does not reject an aware ``datetime`` instance.

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
            "red_team_agent_id": record.red_team_agent_id,
            "executor_agent_id": record.executor_agent_id,
            "red_team_provider": record.red_team_provider,
            "red_team_model_id": record.red_team_model_id,
            "red_team_capability": record.red_team_capability,
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
                report_id=archive_key(row["report_id"]),
                execution_id=str(row["execution_id"]),
                task_id=str(row["task_id"]),
                verdict=RedTeamVerdict(str(row["verdict"])),
                report=report,
                recorded_at=normalize_utc(row["recorded_at"]),
                red_team_agent_id=optional_text(row["red_team_agent_id"]),
                executor_agent_id=optional_text(row["executor_agent_id"]),
                red_team_provider=optional_text(row["red_team_provider"]),
                red_team_model_id=optional_text(row["red_team_model_id"]),
                red_team_capability=optional_capability(row["red_team_capability"]),
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
