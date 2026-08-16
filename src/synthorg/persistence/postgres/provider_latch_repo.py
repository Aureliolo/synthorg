# module-kind: repository
"""Postgres repository for outstanding provider latching failures.

Sibling of :class:`SQLiteProviderLatchRepository` backed by
``psycopg_pool.AsyncConnectionPool``. One upserted row per
``(provider, model)`` pair, replaced by each fresh refusal.
"""

from datetime import datetime
from typing import Final

import psycopg
from psycopg.rows import DictRow, dict_row
from psycopg_pool import AsyncConnectionPool

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.provider import PROVIDER_LATCH_PERSIST_FAILED
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import (
    coerce_row_timestamp,
    format_iso_utc,
    validate_pagination_args,
)
from synthorg.persistence.postgres._integrity import raise_constraint_violation
from synthorg.providers.health import ProviderOutcomeClass
from synthorg.providers.latch import LatchedFailure

logger = get_logger(__name__)

_SELECT_COLS: Final[str] = (
    "provider_name, model, outcome_class, occurred_at, error_message, "
    "response_time_ms, agent_id, task_id"
)

#: Monotonic in ``occurred_at``: the row answers "when did this pair last
#: refuse", and the lookback is measured from it, so an older write landing
#: after a newer one would move the answer backwards and shorten the very
#: window it defines. Two concurrent refusals race their writes (the tracker
#: serialises its in-memory append but not this round trip), and restore
#: re-persists what it read, so the older-arriving-later case is ordinary
#: rather than exotic.
_UPSERT_SQL = f"""
    INSERT INTO provider_latched_failures ({_SELECT_COLS})
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (provider_name, model) DO UPDATE SET
        outcome_class = EXCLUDED.outcome_class,
        occurred_at = EXCLUDED.occurred_at,
        error_message = EXCLUDED.error_message,
        response_time_ms = EXCLUDED.response_time_ms,
        agent_id = EXCLUDED.agent_id,
        task_id = EXCLUDED.task_id
    WHERE EXCLUDED.occurred_at >= provider_latched_failures.occurred_at
"""  # noqa: S608 -- column list is a compile-time constant

_PURGE_SQL = "DELETE FROM provider_latched_failures WHERE occurred_at < %s"


def _params(
    entity: LatchedFailure,
) -> tuple[str, str, str, str, str, float, str | None, str | None]:
    """Return the positional bind parameters for one latch.

    Returns:
        The eight column values in ``_SELECT_COLS`` order.
    """
    return (
        str(entity.provider_name),
        str(entity.model),
        entity.outcome_class.value,
        format_iso_utc(entity.occurred_at),
        str(entity.error_message),
        entity.response_time_ms,
        None if entity.agent_id is None else str(entity.agent_id),
        None if entity.task_id is None else str(entity.task_id),
    )


def _optional(value: object) -> NotBlankStr | None:
    """Return a blank-safe owner id.

    Returns:
        The trimmed id, or ``None`` when the column held no owner.
    """
    if value is None:
        return None
    text = str(value).strip()
    return NotBlankStr(text) if text else None


def _row_to_latch(row: DictRow) -> LatchedFailure:
    """Convert a Postgres dict row into a :class:`LatchedFailure`.

    Returns:
        The parsed latch.

    Raises:
        QueryError: If the row contains corrupt or unparseable data.
    """
    try:
        return LatchedFailure(
            provider_name=NotBlankStr(str(row["provider_name"])),
            model=NotBlankStr(str(row["model"])),
            outcome_class=ProviderOutcomeClass(str(row["outcome_class"])),
            occurred_at=coerce_row_timestamp(row["occurred_at"]),
            error_message=NotBlankStr(str(row["error_message"])),
            response_time_ms=float(row["response_time_ms"]),
            agent_id=_optional(row["agent_id"]),
            task_id=_optional(row["task_id"]),
        )
    except (ValueError, TypeError, KeyError) as exc:
        error_type = type(exc).__name__
        msg = f"Failed to parse provider latch row: {error_type}"
        logger.warning(
            PROVIDER_LATCH_PERSIST_FAILED,
            operation="deserialize",
            error_type=error_type,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc


class PostgresProviderLatchRepository:
    """Postgres-backed store of outstanding latching failures.

    Args:
        pool: Async connection pool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool

    def _log_failure(
        self,
        operation: str,
        exc: Exception,
        *,
        pair: str | None = None,
    ) -> None:
        """Emit the repository-level diagnostic for a failed operation.

        The typed error travels to the caller; this is what an operator
        reading the log sees, and every sibling repository emits it, so a
        database fault here would otherwise be the one that surfaced
        without operational context.
        """
        logger.warning(
            PROVIDER_LATCH_PERSIST_FAILED,
            operation=operation,
            pair=pair,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )

    async def save(self, entity: LatchedFailure) -> None:
        """Upsert one pair's outstanding latch.

        Raises:
            ConstraintViolationError: If a database constraint is violated.
            QueryError: If the database query fails.
        """
        pair = f"{entity.provider_name}/{entity.model}"
        try:
            # The connection context manager rolls back any uncommitted
            # transaction on exception exit, so a failed execute or commit
            # never leaves a half-applied write; the explicit rollback the
            # SQLite arm performs is implicit here.
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(_UPSERT_SQL, _params(entity))
                await conn.commit()
        except psycopg.errors.IntegrityError as exc:
            msg = (
                f"Constraint violation saving provider latch for {pair!r}: "
                f"{safe_error_description(exc)}"
            )
            self._log_failure("save", exc, pair=pair)
            raise_constraint_violation(exc, msg)
        except psycopg.Error as exc:
            msg = (
                f"Failed to save provider latch for {pair!r}: "
                f"{type(exc).__name__} ({safe_error_description(exc)})"
            )
            self._log_failure("save", exc, pair=pair)
            raise QueryError(msg) from exc

    async def get(self, entity_id: tuple[str, str]) -> LatchedFailure | None:
        """Return one pair's outstanding latch, or ``None``.

        Returns:
            The matching latch, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        provider_name, model = entity_id
        sql = (
            f"SELECT {_SELECT_COLS} FROM provider_latched_failures "  # noqa: S608
            "WHERE provider_name = %s AND model = %s"
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (provider_name, model))
                row = await cur.fetchone()
        except psycopg.Error as exc:
            pair = f"{provider_name}/{model}"
            msg = (
                f"Failed to fetch provider latch {pair!r}: "
                f"{type(exc).__name__} ({safe_error_description(exc)})"
            )
            self._log_failure("get", exc, pair=pair)
            raise QueryError(msg) from exc
        return None if row is None else _row_to_latch(row)

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[LatchedFailure, ...]:
        """List latches ordered by pair ascending (paginated).

        Returns:
            The matching latches.

        Raises:
            QueryError: If the database query fails.
        """
        effective_limit = validate_pagination_args(
            limit, offset, event=PROVIDER_LATCH_PERSIST_FAILED
        )
        sql = (
            f"SELECT {_SELECT_COLS} FROM provider_latched_failures "  # noqa: S608
            "ORDER BY provider_name ASC, model ASC LIMIT %s OFFSET %s"
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (effective_limit, offset))
                rows = await cur.fetchall()
            return tuple(_row_to_latch(r) for r in rows)
        except QueryError:
            raise
        except psycopg.Error as exc:
            msg = "Failed to list provider latches"
            self._log_failure("list_items", exc)
            raise QueryError(msg) from exc

    async def delete(self, entity_id: tuple[str, str]) -> bool:
        """Drop one pair's latch.

        Returns:
            ``True`` when a row was deleted, ``False`` when none matched.

        Raises:
            QueryError: If the database query fails.
        """
        provider_name, model = entity_id
        sql = (
            "DELETE FROM provider_latched_failures "
            "WHERE provider_name = %s AND model = %s"
        )
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, (provider_name, model))
                rowcount = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            pair = f"{provider_name}/{model}"
            msg = (
                f"Failed to delete provider latch {pair!r}: "
                f"{type(exc).__name__} ({safe_error_description(exc)})"
            )
            self._log_failure("delete", exc, pair=pair)
            raise QueryError(msg) from exc
        return rowcount > 0

    async def purge_before(self, threshold: datetime) -> int:
        """Drop every latch recorded before *threshold*.

        Returns:
            How many rows were deleted.

        Raises:
            QueryError: If the database query fails.
        """
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(_PURGE_SQL, (threshold,))
                rowcount = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            msg = (
                f"Failed to purge provider latches: "
                f"{type(exc).__name__} ({safe_error_description(exc)})"
            )
            self._log_failure("purge_before", exc)
            raise QueryError(msg) from exc
        return rowcount


__all__ = ["PostgresProviderLatchRepository"]
