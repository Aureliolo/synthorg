# module-kind: repository
"""Postgres repository for the durable org-alert log.

Append-only per :class:`AppendOnlyRepository`. The alert's domain UUID
is the primary key (stored as TEXT, matching the codebase's convention
for domain ids); JSON columns are native JSONB and timestamps are
TIMESTAMPTZ.
"""

from datetime import datetime
from typing import NoReturn
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool

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
from synthorg.persistence._shared import normalize_utc, validate_pagination_args
from synthorg.persistence._shared._filter_clauses import build_alert_filter_clauses
from synthorg.persistence._shared.alert_marshalling import (
    alert_to_payload,
    row_to_alert,
)
from synthorg.persistence.alert_protocol import AlertFilterSpec

logger = get_logger(__name__)

_SELECT_COLS = (
    "id, severity, alert_type, description, affected_domains, "
    "signal_context, recommended_action, emitted_at"
)


def _identity(value: datetime) -> object:
    """Pass a native datetime straight through to the TIMESTAMPTZ column.

    Returns:
        The value unchanged.
    """
    return value


class PostgresAlertRepository:
    """Postgres-backed durable org-alert log.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def append(self, event: Alert) -> None:
        """Persist an alert (append-only).

        Raises:
            QueryError: If the database query fails.
        """
        payload = alert_to_payload(
            event,
            timestamp_serializer=_identity,
            json_serializer=Jsonb,
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    """
                    INSERT INTO org_alerts (
                        id, severity, alert_type, description,
                        affected_domains, signal_context,
                        recommended_action, emitted_at
                    ) VALUES (
                        %(id)s, %(severity)s, %(alert_type)s, %(description)s,
                        %(affected_domains)s, %(signal_context)s,
                        %(recommended_action)s, %(emitted_at)s
                    )
                    """,
                    payload,
                )
                await conn.commit()
        except psycopg.Error as exc:
            self._raise_query_error("save alert", PERSISTENCE_ALERT_SAVE_FAILED, exc)

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
            MalformedRowError: If a returned row fails to parse.
        """
        effective_limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_ALERT_QUERY_FAILED
        )
        clauses, params = build_alert_filter_clauses(
            filter_spec,
            placeholder="%s",
            serialize_severity=lambda s: s.value,
            serialize_timestamp=normalize_utc,
        )
        sql = f"SELECT {_SELECT_COLS} FROM org_alerts"  # noqa: S608
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY emitted_at DESC, id DESC LIMIT %s OFFSET %s"
        params.extend([effective_limit, offset])
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, params)
                rows = await cur.fetchall()
        except psycopg.Error as exc:
            self._raise_query_error("query alerts", PERSISTENCE_ALERT_QUERY_FAILED, exc)
        alerts = tuple(row_to_alert(row) for row in rows)
        logger.debug(PERSISTENCE_ALERT_QUERIED, count=len(alerts))
        return alerts

    async def get_by_id(self, alert_id: UUID) -> Alert | None:
        """Resolve one alert by its domain UUID.

        Returns:
            The matching alert, or ``None`` when no such alert exists.

        Raises:
            QueryError: If the database query fails.
            MalformedRowError: If the returned row fails to parse.
        """
        sql = f"SELECT {_SELECT_COLS} FROM org_alerts WHERE id = %s"  # noqa: S608
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (str(alert_id),))
                row = await cur.fetchone()
        except psycopg.Error as exc:
            self._raise_query_error(
                "fetch alert by id", PERSISTENCE_ALERT_QUERY_FAILED, exc
            )
        return row_to_alert(row) if row is not None else None

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
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(
                    "DELETE FROM org_alerts WHERE emitted_at < %s",
                    (normalize_utc(threshold),),
                )
                deleted = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            self._raise_query_error("purge alerts", PERSISTENCE_ALERT_PURGE_FAILED, exc)
        return deleted

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


__all__ = ["PostgresAlertRepository"]
