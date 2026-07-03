# module-kind: repository
"""SQLite repository for the durable org-alert log.

Append-only per :class:`AppendOnlyRepository`: ``append`` inserts one
immutable alert row keyed by its domain UUID, ``query`` pages
newest-first within optional filters, ``purge_before`` enforces
retention, and ``get_by_id`` resolves a single alert for the
``/meta/chat`` ``alert_id`` routing path. JSON columns are stored as
``json.dumps`` TEXT and timestamps as UTC ISO TEXT.
"""

import json
import sqlite3
from datetime import datetime
from typing import NoReturn
from uuid import UUID

import aiosqlite

from synthorg.core.persistence_errors import QueryError
from synthorg.meta.chief_of_staff.models import Alert
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.alert import (
    PERSISTENCE_ALERT_PURGE_FAILED,
    PERSISTENCE_ALERT_QUERIED,
    PERSISTENCE_ALERT_QUERY_FAILED,
    PERSISTENCE_ALERT_SAVE_FAILED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import (
    format_iso_utc,
    normalize_utc,
    validate_pagination_args,
)
from synthorg.persistence._shared._filter_clauses import build_alert_filter_clauses
from synthorg.persistence._shared.alert_marshalling import (
    alert_to_payload,
    row_to_alert,
)
from synthorg.persistence.alert_protocol import AlertFilterSpec
from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)

_SELECT_COLS = (
    "id, severity, alert_type, description, affected_domains, "
    "signal_context, recommended_action, emitted_at"
)


class SQLiteAlertRepository:
    """SQLite-backed durable org-alert log.

    Args:
        db: An open aiosqlite connection.
        write_context: Async write-serialising context manager.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
    ) -> None:
        self._db = db
        self._db.row_factory = aiosqlite.Row
        self._write_context = write_context

    async def append(self, event: Alert) -> None:
        """Persist an alert (append-only).

        Raises:
            QueryError: If the database query fails.
        """
        payload = alert_to_payload(
            event,
            timestamp_serializer=format_iso_utc,
            json_serializer=lambda v: json.dumps(v, separators=(",", ":")),
        )
        async with self._write_context():
            try:
                await self._db.execute(
                    """
                    INSERT INTO org_alerts (
                        id, severity, alert_type, description,
                        affected_domains, signal_context,
                        recommended_action, emitted_at
                    ) VALUES (
                        :id, :severity, :alert_type, :description,
                        :affected_domains, :signal_context,
                        :recommended_action, :emitted_at
                    )
                    """,
                    payload,
                )
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._rollback("save alert")
                self._raise_query_error(
                    "save alert", PERSISTENCE_ALERT_SAVE_FAILED, exc
                )

    async def query(
        self,
        filter_spec: AlertFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Alert, ...]:
        """Query alerts matching filter spec, newest-first, paginated.

        Returns:
            The matching alerts.

        Raises:
            QueryError: If the query fails or pagination is out of range.
        """
        effective_limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_ALERT_QUERY_FAILED
        )
        clauses, params = build_alert_filter_clauses(
            filter_spec,
            placeholder="?",
            serialize_severity=lambda s: s.value,
            serialize_timestamp=lambda ts: format_iso_utc(normalize_utc(ts)),
        )
        sql = f"SELECT {_SELECT_COLS} FROM org_alerts"  # noqa: S608
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY emitted_at DESC, id DESC LIMIT ? OFFSET ?"
        params.extend([effective_limit, offset])
        try:
            async with self._db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
            alerts = tuple(row_to_alert(dict(row)) for row in rows)
        except (sqlite3.Error, aiosqlite.Error) as exc:
            self._raise_query_error("query alerts", PERSISTENCE_ALERT_QUERY_FAILED, exc)
        logger.debug(PERSISTENCE_ALERT_QUERIED, count=len(alerts))
        return alerts

    async def get_by_id(self, alert_id: UUID) -> Alert | None:
        """Resolve one alert by its domain UUID.

        Returns:
            The matching alert, or ``None`` when no such alert exists.

        Raises:
            QueryError: If the database query fails.
        """
        sql = f"SELECT {_SELECT_COLS} FROM org_alerts WHERE id = ?"  # noqa: S608
        try:
            async with self._db.execute(sql, (str(alert_id),)) as cursor:
                row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            self._raise_query_error(
                "fetch alert by id", PERSISTENCE_ALERT_QUERY_FAILED, exc
            )
        return row_to_alert(dict(row)) if row is not None else None

    async def purge_before(self, threshold: datetime) -> int:
        """Delete alerts emitted before threshold (retention).

        Returns:
            Number of rows removed.

        Raises:
            QueryError: If the threshold is naive or the query fails.
        """
        if threshold.tzinfo is None:
            msg = f"threshold must be timezone-aware, got naive {threshold!r}"
            raise QueryError(msg)
        async with self._write_context():
            try:
                async with self._db.execute(
                    "DELETE FROM org_alerts WHERE emitted_at < ?",
                    (format_iso_utc(normalize_utc(threshold)),),
                ) as cursor:
                    await self._db.commit()
                    return cursor.rowcount
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._rollback("purge alerts")
                self._raise_query_error(
                    "purge alerts", PERSISTENCE_ALERT_PURGE_FAILED, exc
                )

    async def _rollback(self, operation: str) -> None:
        """Roll back the current transaction after a failed write.

        A rollback failure is logged (not raised): the caller is about
        to raise the original error via ``_raise_query_error``, and a
        rollback-of-rollback failure must not mask it.
        """
        try:
            await self._db.rollback()
        except aiosqlite.Error as exc:
            logger.warning(
                PERSISTENCE_ALERT_QUERY_FAILED,
                operation=operation,
                phase="rollback",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    def _raise_query_error(
        self, operation: str, event: str, exc: Exception
    ) -> NoReturn:
        logger.warning(
            event,
            operation=operation,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        msg = f"Failed to {operation}: {type(exc).__name__}"
        raise QueryError(msg) from exc


__all__ = ["SQLiteAlertRepository"]
