# module-kind: repository
"""Postgres repository for the operator-declared failover log.

Sibling of :class:`SQLiteProviderFailoverEventRepository` backed by
``psycopg_pool.AsyncConnectionPool``. Append-only, newest-first.
"""

from datetime import datetime
from typing import Final, LiteralString
from uuid import UUID

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import PROVIDER_FAILOVER_RECORD_FAILED
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import (
    coerce_row_timestamp,
    format_iso_utc,
    normalize_utc,
    validate_pagination_args,
)
from synthorg.persistence.provider_failover_event_protocol import (
    ProviderFailoverEventFilterSpec,
)
from synthorg.providers.failover_event import FailoverStage, ProviderFailoverEvent
from synthorg.providers.health import ProviderOutcomeClass

logger = get_logger(__name__)

_SELECT_COLS: Final[LiteralString] = (
    "id, occurred_at, feature, declared_provider, declared_model, "
    "served_provider, served_model, trigger_class, trigger_stage, "
    "agent_id, task_id"
)

_INSERT_SQL: Final[LiteralString] = f"""
    INSERT INTO provider_failover_events ({_SELECT_COLS})
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
"""  # noqa: S608 -- column list is a compile-time constant


def _params(
    event: ProviderFailoverEvent,
) -> tuple[str, str, str, str, str, str, str, str, str, str | None, str | None]:
    """Return the positional bind parameters for one engagement.

    Returns:
        The eleven column values in ``_SELECT_COLS`` order.
    """
    return (
        str(event.id),
        format_iso_utc(event.occurred_at),
        str(event.feature),
        str(event.declared_provider),
        str(event.declared_model),
        str(event.served_provider),
        str(event.served_model),
        str(event.trigger_class.value),
        event.trigger_stage,
        None if event.agent_id is None else str(event.agent_id),
        None if event.task_id is None else str(event.task_id),
    )


def _optional(value: object) -> NotBlankStr | None:
    """Return a blank-safe owner id.

    Returns:
        The trimmed id, or ``None`` when the column held no owner. A blank
        string is read as absent rather than as an owner named "": an id
        that names no row is worse than no id.
    """
    if value is None:
        return None
    text = str(value).strip()
    return NotBlankStr(text) if text else None


def _row_to_event(row: DictRow) -> ProviderFailoverEvent:
    """Convert a Postgres dict row into a :class:`ProviderFailoverEvent`.

    Returns:
        The parsed engagement.

    Raises:
        QueryError: If the row contains corrupt or unparseable data.
    """
    try:
        stage: FailoverStage = (
            "preflight" if str(row["trigger_stage"]) == "preflight" else "retry"
        )
        return ProviderFailoverEvent(
            id=UUID(str(row["id"])),
            occurred_at=coerce_row_timestamp(row["occurred_at"]),
            feature=NotBlankStr(str(row["feature"])),
            declared_provider=NotBlankStr(str(row["declared_provider"])),
            declared_model=NotBlankStr(str(row["declared_model"])),
            served_provider=NotBlankStr(str(row["served_provider"])),
            served_model=NotBlankStr(str(row["served_model"])),
            trigger_class=ProviderOutcomeClass(str(row["trigger_class"])),
            trigger_stage=stage,
            agent_id=_optional(row["agent_id"]),
            task_id=_optional(row["task_id"]),
        )
    except (ValueError, TypeError, KeyError) as exc:
        error_type = type(exc).__name__
        msg = f"Failed to parse provider failover event row: {error_type}"
        logger.warning(
            PROVIDER_FAILOVER_RECORD_FAILED,
            operation="deserialize",
            error_type=error_type,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc


def _where(
    spec: ProviderFailoverEventFilterSpec,
) -> tuple[LiteralString, list[object]]:
    """Build the WHERE predicate and its bind parameters.

    Every predicate fragment is a literal; only bind parameters carry
    operator input.

    Returns:
        The predicate (``TRUE`` when unfiltered) and its ordered parameters.
    """
    clauses: list[LiteralString] = []
    params: list[object] = []
    if spec.feature is not None:
        clauses.append("feature = %s")
        params.append(str(spec.feature))
    if spec.declared_provider is not None:
        clauses.append("declared_provider = %s")
        params.append(str(spec.declared_provider))
    if spec.since is not None:
        clauses.append("occurred_at >= %s")
        params.append(format_iso_utc(spec.since))
    predicate: LiteralString = " AND ".join(clauses) if clauses else "TRUE"
    return predicate, params


class PostgresProviderFailoverEventRepository:
    """Postgres-backed failover engagement log.

    Args:
        pool: Async connection pool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    async def append(self, event: ProviderFailoverEvent) -> None:
        """Persist one engagement.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            # Here and in the purge below: the connection context manager
            # rolls back any uncommitted transaction on exception exit, so a
            # failed execute or commit never leaves a half-applied write.
            # The explicit rollback the SQLite arm performs is implicit here.
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(_INSERT_SQL, _params(event))
                await conn.commit()
        except psycopg.Error as exc:
            msg = (
                f"Failed to record provider failover event: "
                f"{type(exc).__name__} ({safe_error_description(exc)})"
            )
            raise QueryError(msg) from exc

    async def query(
        self,
        filter_spec: ProviderFailoverEventFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[ProviderFailoverEvent, ...]:
        """Return matching engagements newest-first (paginated).

        Returns:
            The matching engagements.

        Raises:
            QueryError: If the database query fails.
        """
        effective_limit = validate_pagination_args(
            limit, offset, event=PROVIDER_FAILOVER_RECORD_FAILED
        )
        predicate, params = _where(filter_spec)
        sql: LiteralString = (
            f"SELECT {_SELECT_COLS} FROM provider_failover_events "  # noqa: S608
            f"WHERE {predicate} ORDER BY occurred_at DESC, id DESC "
            "LIMIT %s OFFSET %s"
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (*params, effective_limit, offset))
                rows = await cur.fetchall()
            return tuple(_row_to_event(r) for r in rows)
        except QueryError:
            raise
        except psycopg.Error as exc:
            msg = "Failed to query provider failover events"
            logger.warning(
                PROVIDER_FAILOVER_RECORD_FAILED,
                operation="query",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def purge_before(self, threshold: datetime) -> int:
        """Delete engagements older than ``threshold``.

        Returns:
            The number of rows removed.

        Raises:
            QueryError: If the database query fails.
        """
        sql = "DELETE FROM provider_failover_events WHERE occurred_at < %s"
        cutoff = format_iso_utc(normalize_utc(threshold))
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, (cutoff,))
                rowcount = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            msg = (
                f"Failed to purge provider failover events: "
                f"{type(exc).__name__} ({safe_error_description(exc)})"
            )
            raise QueryError(msg) from exc
        return max(0, rowcount)


__all__ = ["PostgresProviderFailoverEventRepository"]
