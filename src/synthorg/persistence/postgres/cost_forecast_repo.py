# module-kind: repository
"""Postgres repository for pre-flight cost forecasts.

Sibling of :class:`SQLiteCostForecastRepository` backed by
``psycopg_pool.AsyncConnectionPool``. Satisfies
``CostForecastRepository`` structurally.

Enforces the same-currency invariant on :meth:`save` against the live
``budget.currency`` setting; mismatches raise
:class:`MixedCurrencyAggregationError` at the repository boundary. Row
<-> model marshalling is shared with the SQLite sibling via
:mod:`synthorg.persistence._shared.cost_forecast_marshalling`.
"""

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

import psycopg
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.budget.errors import MixedCurrencyAggregationError
from synthorg.budget.forecast_models import Forecast, ForecastDecision
from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.cost_forecast import (
    PERSISTENCE_COST_FORECAST_FAILED,
    PERSISTENCE_COST_FORECAST_FETCHED,
    PERSISTENCE_COST_FORECAST_LISTED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import format_iso_utc, validate_pagination_args
from synthorg.persistence._shared.cost_forecast_marshalling import (
    COST_FORECAST_COLUMNS,
    FORECAST_CLEAR_HALT_SQL_PCT,
    build_cost_forecast_where,
    forecast_save_params,
    row_to_forecast,
    validate_cost_forecast_update_keys,
)
from synthorg.persistence.cost_forecast_protocol import CostForecastFilterSpec

logger = get_logger(__name__)

_MAX_PAGE_LIMIT: int = 1_000

_UPSERT_SQL = f"""
    INSERT INTO cost_forecasts ({COST_FORECAST_COLUMNS})
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

    def _check_currency(self, entity: Forecast) -> None:
        """Reject a save whose currency drifts from the live setting.

        Raises:
            MixedCurrencyAggregationError: If the row's currency does not
                match the live ``budget.currency`` setting.
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

    async def save(self, entity: Forecast) -> None:
        """Upsert a forecast row.

        Raises:
            MixedCurrencyAggregationError: If aggregated rows mix currencies.
            ConstraintViolationError: If a database constraint is violated.
            QueryError: If the database query fails.
        """
        self._check_currency(entity)
        params = forecast_save_params(entity)
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
        """Get a forecast by id, or ``None`` if not found.

        Returns:
            The matching entity, or ``None`` when no row matches.

        Raises:
            QueryError: If the database query fails.
        """
        sql = (
            f"SELECT {COST_FORECAST_COLUMNS} FROM cost_forecasts "  # noqa: S608
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
        forecast = row_to_forecast(row)
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
        """List forecasts newest-first.

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        effective_limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_COST_FORECAST_FAILED
        )
        effective_limit = min(effective_limit, _MAX_PAGE_LIMIT)
        sql = (
            f"SELECT {COST_FORECAST_COLUMNS} FROM cost_forecasts "  # noqa: S608
            "ORDER BY created_at DESC, forecast_id DESC LIMIT %s OFFSET %s"
        )
        try:
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(sql, (effective_limit, offset))
                rows = await cur.fetchall()
            items = tuple(row_to_forecast(r) for r in rows)
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
        """Return forecasts matching the spec, newest-first (paginated).

        Returns:
            The matching entities.

        Raises:
            QueryError: If the database query fails.
        """
        effective_limit = validate_pagination_args(
            limit, offset, event=PERSISTENCE_COST_FORECAST_FAILED
        )
        effective_limit = min(effective_limit, _MAX_PAGE_LIMIT)
        where, params = build_cost_forecast_where(filter_spec, placeholder="%s")
        params.extend([effective_limit, offset])
        sql = f"""
            SELECT {COST_FORECAST_COLUMNS} FROM cost_forecasts
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
            items = tuple(row_to_forecast(r) for r in rows)
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
        """Count forecasts matching the filter spec.

        Returns:
            Number of matching rows.

        Raises:
            QueryError: If the database query fails.
        """
        where, params = build_cost_forecast_where(filter_spec, placeholder="%s")
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
        """Atomic compare-and-set for the decision state.

        Returns:
            ``True`` when the operation succeeded, ``False`` otherwise.

        Raises:
            QueryError: If the database query fails.
        """
        validate_cost_forecast_update_keys(
            "transition_if", entity_id, updates, to_state=to_state
        )
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

    async def raise_ceiling_if_halted(
        self,
        entity_id: UUID,
        *,
        new_ceiling: float,
        updated_at: datetime,
    ) -> bool:
        """Atomically raise the ceiling and clear the halt, if still halted.

        Optimistic-concurrency conditional write (ADR-0001 D7): updates
        only while ``halted_at IS NOT NULL``, so a concurrent ceiling
        raise that already resumed the run leaves the row unmatched and
        the second writer is told it lost rather than silently no-op'ing.

        Returns:
            ``True`` when the halted row was updated; ``False`` when the
            row was not halted (already resumed) or is missing.

        Raises:
            QueryError: If the database query fails.
        """
        params = (float(new_ceiling), format_iso_utc(updated_at), str(entity_id))
        try:
            async with self._pool.connection() as conn, conn.cursor() as cur:
                await cur.execute(FORECAST_CLEAR_HALT_SQL_PCT, params)
                rowcount = cur.rowcount
                await conn.commit()
        except psycopg.Error as exc:
            msg = f"Failed to raise ceiling for forecast {entity_id!r}"
            logger.warning(
                PERSISTENCE_COST_FORECAST_FAILED,
                operation="raise_ceiling_if_halted",
                forecast_id=str(entity_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            raise QueryError(msg) from exc
        return rowcount > 0

    async def delete(self, entity_id: UUID) -> bool:
        """Delete a forecast by id.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
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
