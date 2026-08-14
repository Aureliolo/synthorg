# module-kind: repository
"""SQLite repository for the operator-declared failover log.

Append-only. The read side is deliberately newest-first: the operator
question this answers is "is my declared pair failing right now", and the
answer ages out of usefulness within hours.
"""

import sqlite3
from datetime import datetime
from typing import Final, LiteralString
from uuid import UUID

import aiosqlite
from aiosqlite import Row

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.provider import PROVIDER_FAILOVER_RECORD_FAILED
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import (
    coerce_row_timestamp,
    format_iso_utc,
    require_aware_utc,
    validate_pagination_args,
)
from synthorg.persistence.provider_failover_event_protocol import (
    ProviderFailoverEventFilterSpec,
)
from synthorg.persistence.sqlite._shared import WriteContext
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
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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


def _row_to_event(row: Row) -> ProviderFailoverEvent:
    """Convert a database row into a :class:`ProviderFailoverEvent`.

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
    except (ValueError, TypeError, KeyError, IndexError) as exc:
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
        clauses.append("feature = ?")
        params.append(str(spec.feature))
    if spec.declared_provider is not None:
        clauses.append("declared_provider = ?")
        params.append(str(spec.declared_provider))
    if spec.since is not None:
        clauses.append("occurred_at >= ?")
        params.append(format_iso_utc(spec.since))
    predicate: LiteralString = " AND ".join(clauses) if clauses else "TRUE"
    return predicate, params


class SQLiteProviderFailoverEventRepository:
    """SQLite-backed failover engagement log.

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

    async def append(self, event: ProviderFailoverEvent) -> None:
        """Persist one engagement.

        Raises:
            QueryError: If the database query fails.
        """
        async with self._write_context():
            try:
                await self._db.execute(_INSERT_SQL, _params(event))
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._rollback()
                msg = (
                    f"Failed to record provider failover event: "
                    f"{type(exc).__name__} ({safe_error_description(exc)})"
                )
                self._log_failure("append", exc)
                raise QueryError(msg) from exc

    def _log_failure(self, operation: str, exc: Exception) -> None:
        """Emit the repository-level diagnostic for a failed operation.

        The typed error travels to the caller; this is what an operator
        reading the log sees, and every sibling repository emits it, so a
        database fault here would otherwise be the one that surfaced
        without operational context.
        """
        logger.warning(
            PROVIDER_FAILOVER_RECORD_FAILED,
            operation=operation,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )

    async def _rollback(self) -> None:
        """Roll back the current transaction, logging any rollback failure."""
        try:
            await self._db.rollback()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            log_exception_redacted(
                logger,
                PROVIDER_FAILOVER_RECORD_FAILED,
                exc,
                phase="rollback",
            )

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
            "LIMIT ? OFFSET ?"
        )
        try:
            async with self._db.execute(
                sql, (*params, effective_limit, offset)
            ) as cursor:
                rows = await cursor.fetchall()
            return tuple(_row_to_event(r) for r in rows)
        except QueryError:
            raise
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to query provider failover events"
            self._log_failure("query", exc)
            raise QueryError(msg) from exc

    async def purge_before(self, threshold: datetime) -> int:
        """Delete engagements older than ``threshold``.

        Returns:
            The number of rows removed.

        Raises:
            QueryError: If ``threshold`` is naive, or the database query
                fails. A naive threshold is refused rather than read as UTC:
                local wall-clock time taken for UTC deletes a different set
                of rows than the retention window asked for, silently.
        """
        sql = "DELETE FROM provider_failover_events WHERE occurred_at < ?"
        try:
            cutoff = format_iso_utc(require_aware_utc(threshold, field="threshold"))
        except ValueError as exc:
            self._log_failure("purge_before", exc)
            raise QueryError(str(exc)) from exc
        async with self._write_context():
            try:
                async with self._db.execute(sql, (cutoff,)) as cursor:
                    rowcount = cursor.rowcount
                    await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await self._rollback()
                msg = (
                    f"Failed to purge provider failover events: "
                    f"{type(exc).__name__} ({safe_error_description(exc)})"
                )
                self._log_failure("purge_before", exc)
                raise QueryError(msg) from exc
        return max(0, rowcount)


__all__ = ["SQLiteProviderFailoverEventRepository"]
