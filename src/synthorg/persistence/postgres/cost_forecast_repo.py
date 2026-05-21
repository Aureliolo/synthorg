"""Postgres repository for pre-flight cost forecasts.

Sibling of :class:`SQLiteCostForecastRepository` backed by
``psycopg_pool.AsyncConnectionPool``. Satisfies
``CostForecastRepository`` structurally.

Enforces the same-currency invariant on :meth:`save` against the live
``budget.currency`` setting; mismatches raise
:class:`MixedCurrencyAggregationError` at the repository boundary.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

import psycopg
from psycopg.rows import dict_row

from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.budget.errors import MixedCurrencyAggregationError
from synthorg.budget.forecast_models import (
    Forecast,
    ForecastDecision,
    HaltContext,
)
from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_COST_FORECAST_FAILED,
    PERSISTENCE_COST_FORECAST_FETCHED,
    PERSISTENCE_COST_FORECAST_LISTED,
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

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any

    from psycopg_pool import AsyncConnectionPool

logger = get_logger(__name__)

_MAX_PAGE_LIMIT: int = 1_000

_SELECT_COLS = (
    "forecast_id, brief_hash, estimated_cost, lower_bound, upper_bound, "
    "currency, decision, decided_at, decided_by, ceiling_amount, "
    "halt_accumulated_cost, halt_ceiling_amount, halt_currency, halted_at, "
    "created_at, updated_at"
)

_UPSERT_SQL = f"""
    INSERT INTO cost_forecasts ({_SELECT_COLS})
    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
    ON CONFLICT (forecast_id) DO UPDATE SET
        brief_hash = EXCLUDED.brief_hash,
        estimated_cost = EXCLUDED.estimated_cost,
        lower_bound = EXCLUDED.lower_bound,
        upper_bound = EXCLUDED.upper_bound,
        currency = EXCLUDED.currency,
        decision = EXCLUDED.decision,
        decided_at = EXCLUDED.decided_at,
        decided_by = EXCLUDED.decided_by,
        ceiling_amount = EXCLUDED.ceiling_amount,
        halt_accumulated_cost = EXCLUDED.halt_accumulated_cost,
        halt_ceiling_amount = EXCLUDED.halt_ceiling_amount,
        halt_currency = EXCLUDED.halt_currency,
        halted_at = EXCLUDED.halted_at,
        updated_at = EXCLUDED.updated_at
"""  # noqa: S608 -- column list is a compile-time constant


def _row_to_forecast(row: dict[str, Any]) -> Forecast:
    """Convert a Postgres dict row into a :class:`Forecast`."""
    try:
        decided_at_raw = row["decided_at"]
        halted_at_raw = row["halted_at"]
        halt_context = (
            HaltContext(
                accumulated_cost=float(row["halt_accumulated_cost"]),
                ceiling_amount=float(row["halt_ceiling_amount"]),
                currency=str(row["halt_currency"]),
                halted_at=coerce_row_timestamp(halted_at_raw),
            )
            if halted_at_raw is not None
            else None
        )
        return Forecast(
            forecast_id=(
                row["forecast_id"]
                if isinstance(row["forecast_id"], UUID)
                else UUID(row["forecast_id"])
            ),
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
            halt_context=halt_context,
            created_at=coerce_row_timestamp(row["created_at"]),
            updated_at=coerce_row_timestamp(row["updated_at"]),
        )
    except (ValueError, TypeError, KeyError) as exc:
        msg = (
            f"Failed to parse cost forecast row: "
            f"{type(exc).__name__} ({safe_error_description(exc)})"
        )
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
        clauses.append("brief_hash = %s")
        params.append(filter_spec.brief_hash)
    if filter_spec.decision is not None:
        clauses.append("decision = %s")
        params.append(filter_spec.decision.value)
    where = " AND ".join(clauses) if clauses else "TRUE"
    return where, params


def _validate_update_keys(
    operation: str,
    forecast_id: UUID,
    updates: dict[str, object],
    *,
    to_state: ForecastDecision,
) -> None:
    """Reject unknown update keys; enforce ``superseded`` semantics."""
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


class PostgresCostForecastRepository:
    """Postgres-backed cost forecast repository.

    Args:
        pool: Async connection pool.
        currency_getter: Callable returning the live ``budget.currency``
            setting; consulted on every save so a stale config value is
            never silently honoured. Defaults to a sentinel returning
            :data:`DEFAULT_CURRENCY` for tests that do not wire a
            settings service.
    """

    def __init__(
        self,
        pool: AsyncConnectionPool,
        *,
        currency_getter: Callable[[], str] | None = None,
    ) -> None:
        self._pool = pool
        self._currency_getter: Callable[[], str] = (
            currency_getter if currency_getter is not None else lambda: DEFAULT_CURRENCY
        )

    async def save(self, entity: Forecast) -> None:
        """Upsert a forecast row."""
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
            (
                float(entity.halt_context.accumulated_cost)
                if entity.halt_context is not None
                else None
            ),
            (
                float(entity.halt_context.ceiling_amount)
                if entity.halt_context is not None
                else None
            ),
            (entity.halt_context.currency if entity.halt_context is not None else None),
            (
                format_iso_utc(entity.halt_context.halted_at)
                if entity.halt_context is not None
                else None
            ),
            format_iso_utc(entity.created_at),
            format_iso_utc(entity.updated_at),
        )
        try:
            # The connection context manager rolls back any uncommitted
            # transaction on exception exit, so a failed execute/commit
            # below never leaves a half-applied write (the explicit
            # rollback the SQLite arm performs is implicit here).
            async with self._pool.connection() as conn:
                await conn.execute(_UPSERT_SQL, params)
                await conn.commit()
        except psycopg.errors.IntegrityError as exc:
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
        except psycopg.Error as exc:
            msg = (
                f"Failed to save forecast {entity.forecast_id!r}: "
                f"{type(exc).__name__} ({safe_error_description(exc)})"
            )
            logger.warning(
                PERSISTENCE_COST_FORECAST_FAILED,
                operation="save",
                forecast_id=str(entity.forecast_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc

    async def get(self, entity_id: UUID) -> Forecast | None:
        """Get a forecast by id, or ``None`` if not found."""
        sql = (
            f"SELECT {_SELECT_COLS} FROM cost_forecasts "  # noqa: S608
            "WHERE forecast_id = %s"
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (str(entity_id),))
                row = await cur.fetchone()
        except psycopg.Error as exc:
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
        """List forecasts newest-first."""
        effective_limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_COST_FORECAST_FAILED
        )
        effective_limit = min(effective_limit, _MAX_PAGE_LIMIT)
        sql = (
            f"SELECT {_SELECT_COLS} FROM cost_forecasts "  # noqa: S608
            "ORDER BY created_at DESC, forecast_id DESC LIMIT %s OFFSET %s"
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (effective_limit, offset))
                rows = await cur.fetchall()
            items = tuple(_row_to_forecast(r) for r in rows)
        except QueryError:
            raise
        except psycopg.Error as exc:
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
            LIMIT %s OFFSET %s
        """  # noqa: S608 -- ``where`` is a closed set of column predicates
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, params)
                rows = await cur.fetchall()
            items = tuple(_row_to_forecast(r) for r in rows)
        except QueryError:
            raise
        except psycopg.Error as exc:
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
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, params)
                row = await cur.fetchone()
                assert row is not None  # noqa: S101 -- COUNT always returns a row
                return int(row[0])
        except psycopg.Error as exc:
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
        """Atomic compare-and-set for the decision state."""
        _validate_update_keys("transition_if", entity_id, updates, to_state=to_state)
        decided_by = updates.get("decided_by")
        decided_at_raw = updates.get("decided_at")
        ceiling_amount = updates.get("ceiling_amount")
        decided_at_value: str | None = None
        # The chk_cf_decision_timestamp constraint requires a superseded
        # row to carry decided_at (the supersede time) with decided_by
        # NULL; it is decided_by absence, not decided_at, that marks a
        # system supersede apart from an operator approve/reject.
        if to_state in {ForecastDecision.APPROVED, ForecastDecision.REJECTED}:
            decided_at_value = format_iso_utc(
                decided_at_raw
                if isinstance(decided_at_raw, datetime)
                else datetime.now(UTC),
            )
        elif to_state is ForecastDecision.SUPERSEDED:
            decided_at_value = format_iso_utc(datetime.now(UTC))
        updated_at_value = format_iso_utc(datetime.now(UTC))
        sql = (
            "UPDATE cost_forecasts SET "
            "decision = %s, decided_at = %s, decided_by = %s, "
            "ceiling_amount = COALESCE(%s, ceiling_amount), updated_at = %s "
            "WHERE forecast_id = %s AND decision = %s"
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
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, params)
                rowcount = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to transition forecast {entity_id!r}"
            logger.warning(
                PERSISTENCE_COST_FORECAST_FAILED,
                operation="transition_if",
                forecast_id=str(entity_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return rowcount > 0

    async def delete(self, entity_id: UUID) -> bool:
        """Delete a forecast by id."""
        sql = "DELETE FROM cost_forecasts WHERE forecast_id = %s"
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(sql, (str(entity_id),))
                rowcount = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to delete forecast {entity_id!r}"
            logger.warning(
                PERSISTENCE_COST_FORECAST_FAILED,
                operation="delete",
                forecast_id=str(entity_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return rowcount > 0


__all__ = ["PostgresCostForecastRepository"]
