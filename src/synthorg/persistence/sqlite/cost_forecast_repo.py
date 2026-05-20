"""SQLite repository for pre-flight cost forecasts.

Satisfies ``CostForecastRepository`` structurally: id-keyed CRUD, atomic
state transitions (``pending -> approved | rejected | superseded``), and
filtered queries by ``brief_hash`` / ``decision``.

The save path enforces the same-currency invariant against the live
``budget.currency`` setting (mirrors :meth:`CostTracker.record`); a
mismatch raises :class:`MixedCurrencyAggregationError` at the
repository boundary so silent re-stamping cannot poison aggregates.
"""

import sqlite3
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

import aiosqlite
from aiosqlite import Row

from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.budget.errors import MixedCurrencyAggregationError
from synthorg.budget.forecast_models import Forecast, ForecastDecision
from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_COST_FORECAST_FAILED,
    PERSISTENCE_COST_FORECAST_FETCHED,
    PERSISTENCE_COST_FORECAST_LISTED,
    PERSISTENCE_COST_FORECAST_SAVED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import (
    coerce_row_timestamp,
    format_iso_utc,
    validate_pagination_args,
)
from synthorg.persistence.cost_forecast_protocol import (  # noqa: TC001
    CostForecastFilterSpec,
)
from synthorg.persistence.sqlite._shared import WriteContext  # noqa: TC001

if TYPE_CHECKING:
    from collections.abc import Callable

logger = get_logger(__name__)

_MAX_PAGE_LIMIT: int = 1_000

_SELECT_COLS = (
    "forecast_id, brief_hash, estimated_cost, lower_bound, upper_bound, "
    "currency, decision, decided_at, decided_by, ceiling_amount, "
    "created_at, updated_at"
)

_UPSERT_SQL = f"""
    INSERT INTO cost_forecasts ({_SELECT_COLS})
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    ON CONFLICT(forecast_id) DO UPDATE SET
        brief_hash = excluded.brief_hash,
        estimated_cost = excluded.estimated_cost,
        lower_bound = excluded.lower_bound,
        upper_bound = excluded.upper_bound,
        currency = excluded.currency,
        decision = excluded.decision,
        decided_at = excluded.decided_at,
        decided_by = excluded.decided_by,
        ceiling_amount = excluded.ceiling_amount,
        updated_at = excluded.updated_at
"""  # noqa: S608 -- column list is a compile-time constant


async def _safe_rollback(
    db: aiosqlite.Connection,
    *,
    operation: str,
    **log_context: object,
) -> None:
    """Roll back the current transaction, logging any rollback failure."""
    try:
        await db.rollback()
    except MemoryError, RecursionError:
        raise
    except (sqlite3.Error, aiosqlite.Error) as rollback_exc:
        logger.error(
            PERSISTENCE_COST_FORECAST_FAILED,
            phase="rollback",
            operation=operation,
            error_type=type(rollback_exc).__name__,
            error=safe_error_description(rollback_exc),
            **log_context,
        )


def _row_to_forecast(row: Row) -> Forecast:
    """Convert a database row into a :class:`Forecast`.

    Raises:
        QueryError: If the row contains corrupt or unparseable data.
    """
    try:
        decided_at_raw = row["decided_at"]
        return Forecast(
            forecast_id=UUID(str(row["forecast_id"])),
            brief_hash=str(row["brief_hash"]),
            estimated_cost=float(row["estimated_cost"]),
            lower_bound=float(row["lower_bound"]),
            upper_bound=float(row["upper_bound"]),
            currency=str(row["currency"]),
            decision=ForecastDecision(str(row["decision"])),
            decided_at=(
                coerce_row_timestamp(decided_at_raw)
                if decided_at_raw is not None
                else None
            ),
            decided_by=(
                str(row["decided_by"]) if row["decided_by"] is not None else None
            ),
            ceiling_amount=(
                float(row["ceiling_amount"])
                if row["ceiling_amount"] is not None
                else None
            ),
            created_at=coerce_row_timestamp(row["created_at"]),
            updated_at=coerce_row_timestamp(row["updated_at"]),
        )
    except (ValueError, TypeError, KeyError) as exc:
        msg = "Failed to parse cost forecast row"
        logger.warning(
            PERSISTENCE_COST_FORECAST_FAILED,
            operation="deserialize",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc


def _build_where(
    filter_spec: CostForecastFilterSpec,
) -> tuple[str, list[object]]:
    """Build the WHERE clause + bound params from a filter spec."""
    clauses: list[str] = []
    params: list[object] = []
    if filter_spec.brief_hash is not None:
        clauses.append("brief_hash = ?")
        params.append(filter_spec.brief_hash)
    if filter_spec.decision is not None:
        clauses.append("decision = ?")
        params.append(filter_spec.decision.value)
    where = " AND ".join(clauses) if clauses else "1=1"
    return where, params


def _validate_update_keys(
    operation: str,
    forecast_id: UUID,
    updates: dict[str, object],
    *,
    to_state: ForecastDecision,
) -> None:
    """Reject unknown update keys; validate decided_by semantics.

    ``superseded`` is a system transition (the operator edited the
    brief); ``decided_by`` is forbidden there.
    """
    allowed = {"decided_by", "decided_at", "ceiling_amount"}
    unknown = sorted(set(updates) - allowed)
    if unknown:
        msg = f"transition_if rejects unknown update keys: {unknown!r}"
        logger.warning(
            PERSISTENCE_COST_FORECAST_FAILED,
            operation=operation,
            forecast_id=str(forecast_id),
            error=msg,
        )
        raise QueryError(msg)
    if to_state is ForecastDecision.SUPERSEDED and "decided_by" in updates:
        msg = "transition to 'superseded' must not carry decided_by"
        logger.warning(
            PERSISTENCE_COST_FORECAST_FAILED,
            operation=operation,
            forecast_id=str(forecast_id),
            error=msg,
        )
        raise QueryError(msg)


class SQLiteCostForecastRepository:
    """SQLite-backed cost forecast repository.

    Args:
        db: An open aiosqlite connection.
        write_context: Async write-serialising context manager.
        currency_getter: Callable returning the live ``budget.currency``
            setting; consulted on every save so a stale config value
            is never silently honoured. Defaults to a sentinel that
            returns :data:`DEFAULT_CURRENCY` for tests that do not
            wire a settings service.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_context: WriteContext,
        currency_getter: Callable[[], str] | None = None,
    ) -> None:
        self._db = db
        self._db.row_factory = aiosqlite.Row
        self._write_context = write_context
        self._currency_getter: Callable[[], str] = (
            currency_getter if currency_getter is not None else lambda: DEFAULT_CURRENCY
        )

    async def save(self, entity: Forecast) -> None:
        """Upsert a forecast row.

        Raises:
            MixedCurrencyAggregationError: If the row's currency does
                not match the live ``budget.currency`` setting.
            ConstraintViolationError: On constraint violations.
            QueryError: On other database errors.
        """
        live_currency = self._currency_getter()
        if entity.currency != live_currency:
            logger.warning(
                PERSISTENCE_COST_FORECAST_FAILED,
                operation="save",
                forecast_id=str(entity.forecast_id),
                reason="currency_mismatch",
                row_currency=entity.currency,
                live_currency=live_currency,
            )
            msg = "Forecast currency does not match live budget.currency"
            raise MixedCurrencyAggregationError(
                msg,
                currencies=frozenset({entity.currency, live_currency}),
            )
        params = (
            str(entity.forecast_id),
            entity.brief_hash,
            float(entity.estimated_cost),
            float(entity.lower_bound),
            float(entity.upper_bound),
            entity.currency,
            entity.decision.value,
            (
                format_iso_utc(entity.decided_at)
                if entity.decided_at is not None
                else None
            ),
            entity.decided_by,
            (
                float(entity.ceiling_amount)
                if entity.ceiling_amount is not None
                else None
            ),
            format_iso_utc(entity.created_at),
            format_iso_utc(entity.updated_at),
        )
        async with self._write_context():
            try:
                await self._db.execute(_UPSERT_SQL, params)
                await self._db.commit()
            except sqlite3.IntegrityError as exc:
                await _safe_rollback(
                    self._db,
                    operation="save",
                    forecast_id=str(entity.forecast_id),
                )
                msg = (
                    f"Constraint violation saving forecast {entity.forecast_id!r}: "
                    f"{safe_error_description(exc)}"
                )
                logger.warning(
                    PERSISTENCE_COST_FORECAST_FAILED,
                    operation="save",
                    forecast_id=str(entity.forecast_id),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise ConstraintViolationError(msg, constraint=str(exc)) from exc
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await _safe_rollback(
                    self._db,
                    operation="save",
                    forecast_id=str(entity.forecast_id),
                )
                msg = f"Failed to save forecast {entity.forecast_id!r}"
                logger.warning(
                    PERSISTENCE_COST_FORECAST_FAILED,
                    operation="save",
                    forecast_id=str(entity.forecast_id),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        logger.debug(
            PERSISTENCE_COST_FORECAST_SAVED,
            forecast_id=str(entity.forecast_id),
            decision=entity.decision.value,
        )

    async def get(self, entity_id: UUID) -> Forecast | None:
        """Get a forecast by id, or ``None`` if not found."""
        sql = (
            f"SELECT {_SELECT_COLS} FROM cost_forecasts "  # noqa: S608
            "WHERE forecast_id = ?"
        )
        try:
            cursor = await self._db.execute(sql, (str(entity_id),))
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = f"Failed to fetch forecast {entity_id!r}"
            logger.warning(
                PERSISTENCE_COST_FORECAST_FAILED,
                operation="get",
                forecast_id=str(entity_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        if row is None:
            return None
        forecast = _row_to_forecast(row)
        logger.debug(
            PERSISTENCE_COST_FORECAST_FETCHED,
            forecast_id=str(entity_id),
        )
        return forecast

    async def list_items(
        self,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Forecast, ...]:
        """List forecasts newest-first (``created_at DESC, forecast_id DESC``)."""
        effective_limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_COST_FORECAST_FAILED
        )
        effective_limit = min(effective_limit, _MAX_PAGE_LIMIT)
        sql = (
            f"SELECT {_SELECT_COLS} FROM cost_forecasts "  # noqa: S608
            "ORDER BY created_at DESC, forecast_id DESC LIMIT ? OFFSET ?"
        )
        try:
            cursor = await self._db.execute(sql, (effective_limit, offset))
            rows = await cursor.fetchall()
            items = tuple(_row_to_forecast(r) for r in rows)
        except QueryError:
            raise
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to list forecasts"
            logger.warning(
                PERSISTENCE_COST_FORECAST_FAILED,
                operation="list_items",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_COST_FORECAST_LISTED, count=len(items))
        return items

    async def query(
        self,
        filter_spec: CostForecastFilterSpec,
        *,
        limit: int = DEFAULT_PAGE_SIZE,
        offset: int = 0,
    ) -> tuple[Forecast, ...]:
        """Return forecasts matching the spec, newest-first (paginated)."""
        effective_limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_COST_FORECAST_FAILED
        )
        effective_limit = min(effective_limit, _MAX_PAGE_LIMIT)
        where, params = _build_where(filter_spec)
        params.extend([effective_limit, offset])
        sql = f"""
            SELECT {_SELECT_COLS} FROM cost_forecasts
            WHERE {where}
            ORDER BY created_at DESC, forecast_id DESC
            LIMIT ? OFFSET ?
        """  # noqa: S608 -- ``where`` is a closed set of column predicates
        try:
            cursor = await self._db.execute(sql, params)
            rows = await cursor.fetchall()
            items = tuple(_row_to_forecast(r) for r in rows)
        except QueryError:
            raise
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to query forecasts"
            logger.warning(
                PERSISTENCE_COST_FORECAST_FAILED,
                operation="query",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        logger.debug(PERSISTENCE_COST_FORECAST_LISTED, count=len(items))
        return items

    async def count(self, filter_spec: CostForecastFilterSpec) -> int:
        """Count forecasts matching the filter spec."""
        where, params = _build_where(filter_spec)
        sql = (
            "SELECT COUNT(*) FROM cost_forecasts "  # noqa: S608
            f"WHERE {where}"
        )
        try:
            cursor = await self._db.execute(sql, params)
            row = await cursor.fetchone()
            assert row is not None  # noqa: S101 -- COUNT always returns a row
            return int(row[0])
        except (sqlite3.Error, aiosqlite.Error) as exc:
            msg = "Failed to count forecasts"
            logger.warning(
                PERSISTENCE_COST_FORECAST_FAILED,
                operation="count",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def transition_if(
        self,
        entity_id: UUID,
        from_state: ForecastDecision,
        to_state: ForecastDecision,
        **updates: object,
    ) -> bool:
        """Atomic compare-and-set for the decision state.

        Accepts ``decided_by`` (NotBlankStr), ``decided_at`` (UTC
        datetime), and ``ceiling_amount`` (float) as correlated
        updates. ``decided_at`` defaults to ``utcnow()`` when omitted.
        """
        _validate_update_keys("transition_if", entity_id, updates, to_state=to_state)
        decided_by = updates.get("decided_by")
        decided_at_raw = updates.get("decided_at")
        ceiling_amount = updates.get("ceiling_amount")
        decided_at_value: str | None = None
        if to_state in {ForecastDecision.APPROVED, ForecastDecision.REJECTED}:
            decided_at_dt = (
                decided_at_raw
                if isinstance(decided_at_raw, datetime)
                else datetime.now(UTC)
            )
            decided_at_value = format_iso_utc(decided_at_dt)
        elif to_state is ForecastDecision.SUPERSEDED:
            decided_at_value = format_iso_utc(datetime.now(UTC))
        updated_at_value = format_iso_utc(datetime.now(UTC))
        sql = (
            "UPDATE cost_forecasts SET "
            "decision = ?, decided_at = ?, decided_by = ?, "
            "ceiling_amount = COALESCE(?, ceiling_amount), updated_at = ? "
            "WHERE forecast_id = ? AND decision = ?"
        )
        params = (
            to_state.value,
            decided_at_value,
            decided_by,
            ceiling_amount,
            updated_at_value,
            str(entity_id),
            from_state.value,
        )
        async with self._write_context():
            try:
                cursor = await self._db.execute(sql, params)
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await _safe_rollback(
                    self._db,
                    operation="transition_if",
                    forecast_id=str(entity_id),
                )
                msg = f"Failed to transition forecast {entity_id!r}"
                logger.warning(
                    PERSISTENCE_COST_FORECAST_FAILED,
                    operation="transition_if",
                    forecast_id=str(entity_id),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return cursor.rowcount > 0

    async def delete(self, entity_id: UUID) -> bool:
        """Delete a forecast by id."""
        sql = "DELETE FROM cost_forecasts WHERE forecast_id = ?"
        async with self._write_context():
            try:
                cursor = await self._db.execute(sql, (str(entity_id),))
                await self._db.commit()
            except (sqlite3.Error, aiosqlite.Error) as exc:
                await _safe_rollback(
                    self._db, operation="delete", forecast_id=str(entity_id)
                )
                msg = f"Failed to delete forecast {entity_id!r}"
                logger.warning(
                    PERSISTENCE_COST_FORECAST_FAILED,
                    operation="delete",
                    forecast_id=str(entity_id),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                raise QueryError(msg) from exc
        return cursor.rowcount > 0


__all__ = ["SQLiteCostForecastRepository"]
