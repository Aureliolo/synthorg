# module-kind: repository
"""SQLite repository for pre-flight cost forecasts.

Satisfies ``CostForecastRepository`` structurally: id-keyed CRUD, atomic
state transitions (``pending -> approved | rejected | superseded``), and
filtered queries by ``brief_hash`` / ``decision``.

The save path enforces the same-currency invariant against the live
``budget.currency`` setting (mirrors :meth:`CostTracker.record`); a
mismatch raises :class:`MixedCurrencyAggregationError` at the
repository boundary so silent re-stamping cannot poison aggregates. Row
<-> model marshalling is shared with the Postgres sibling via
:mod:`synthorg.persistence._shared.cost_forecast_marshalling`.
"""

import sqlite3
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import UUID

import aiosqlite

from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.budget.errors import MixedCurrencyAggregationError
from synthorg.budget.forecast_models import Forecast, ForecastDecision
from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.observability import (
    get_logger,
    log_exception_redacted,
    safe_error_description,
)
from synthorg.observability.events.persistence.cost_forecast import (
    PERSISTENCE_COST_FORECAST_FAILED,
    PERSISTENCE_COST_FORECAST_FETCHED,
    PERSISTENCE_COST_FORECAST_LISTED,
)
from synthorg.persistence._generics import DEFAULT_PAGE_SIZE
from synthorg.persistence._shared import format_iso_utc, validate_pagination_args
from synthorg.persistence._shared.cost_forecast_marshalling import (
    COST_FORECAST_COLUMNS,
    build_cost_forecast_where,
    forecast_save_params,
    row_to_forecast,
    validate_cost_forecast_update_keys,
)
from synthorg.persistence.cost_forecast_protocol import CostForecastFilterSpec
from synthorg.persistence.sqlite._shared import WriteContext

logger = get_logger(__name__)

_MAX_PAGE_LIMIT: int = 1_000

_UPSERT_SQL = f"""
    INSERT INTO cost_forecasts ({COST_FORECAST_COLUMNS})
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
        halt_accumulated_cost = excluded.halt_accumulated_cost,
        halt_ceiling_amount = excluded.halt_ceiling_amount,
        halt_currency = excluded.halt_currency,
        halted_at = excluded.halted_at,
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
    except (sqlite3.Error, aiosqlite.Error) as rollback_exc:
        log_exception_redacted(
            logger,
            PERSISTENCE_COST_FORECAST_FAILED,
            rollback_exc,
            phase="rollback",
            operation=operation,
            **log_context,
        )


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
            MixedCurrencyAggregationError: If the row's currency does
                not match the live ``budget.currency`` setting.
            ConstraintViolationError: On constraint violations.
            QueryError: On other database errors.
        """
        self._check_currency(entity)
        params = forecast_save_params(entity)
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
            "WHERE forecast_id = ?"
        )
        try:
            async with self._db.execute(sql, (str(entity_id),)) as cursor:
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
        """List forecasts newest-first (``created_at DESC, forecast_id DESC``).

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
            "ORDER BY created_at DESC, forecast_id DESC LIMIT ? OFFSET ?"
        )
        try:
            async with self._db.execute(sql, (effective_limit, offset)) as cursor:
                rows = await cursor.fetchall()
            items = tuple(row_to_forecast(r) for r in rows)
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
        where, params = build_cost_forecast_where(filter_spec, placeholder="?")
        params.extend([effective_limit, offset])
        sql = f"""
            SELECT {COST_FORECAST_COLUMNS} FROM cost_forecasts
            WHERE {where}
            ORDER BY created_at DESC, forecast_id DESC
            LIMIT ? OFFSET ?
        """  # noqa: S608 -- ``where`` is a closed set of column predicates
        try:
            async with self._db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
            items = tuple(row_to_forecast(r) for r in rows)
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
        """Count forecasts matching the filter spec.

        Returns:
            Number of matching rows.

        Raises:
            QueryError: If the database query fails.
        """
        where, params = build_cost_forecast_where(filter_spec, placeholder="?")
        sql = (
            "SELECT COUNT(*) FROM cost_forecasts "  # noqa: S608
            f"WHERE {where}"
        )
        try:
            async with self._db.execute(sql, params) as cursor:
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
                async with self._db.execute(sql, params) as cursor:
                    await self._db.commit()
                    _db_rowcount = cursor.rowcount
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
        return _db_rowcount > 0

    async def delete(self, entity_id: UUID) -> bool:
        """Delete a forecast by id.

        Returns:
            ``True`` when a row was deleted, ``False`` if no matching row existed.

        Raises:
            QueryError: If the database query fails.
        """
        sql = "DELETE FROM cost_forecasts WHERE forecast_id = ?"
        async with self._write_context():
            try:
                async with self._db.execute(sql, (str(entity_id),)) as cursor:
                    await self._db.commit()
                    _db_rowcount = cursor.rowcount
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
        return _db_rowcount > 0


__all__ = ["SQLiteCostForecastRepository"]
