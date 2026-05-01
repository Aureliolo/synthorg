"""SQLite repository for durable project cost aggregates.

The schema does not yet carry a ``currency`` column -- that work is
queued under #1597 and requires an Atlas migration.  Until that
column exists, this repo enforces the same-currency invariant with a
process-local ``_pinned_currencies`` map keyed by ``project_id``,
mirroring the in-memory pin pattern in :class:`CostTracker`.  A
single WARNING is emitted on first construction so operators know
the durable pin is missing; once #1597 lands the column the pin
becomes redundant and is removed.
"""

import asyncio
import math
import sqlite3
from datetime import UTC, datetime

import aiosqlite
from pydantic import ValidationError

from synthorg.budget.currency import DEFAULT_CURRENCY, CurrencyCode
from synthorg.budget.errors import MixedCurrencyAggregationError
from synthorg.budget.project_cost_aggregate import ProjectCostAggregate
from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_PROJECT_COST_AGG_CURRENCY_PIN_MISSING,
    PERSISTENCE_PROJECT_COST_AGG_DESERIALIZE_FAILED,
    PERSISTENCE_PROJECT_COST_AGG_FETCH_FAILED,
    PERSISTENCE_PROJECT_COST_AGG_FETCHED,
    PERSISTENCE_PROJECT_COST_AGG_INCREMENT_FAILED,
    PERSISTENCE_PROJECT_COST_AGG_INCREMENTED,
)
from synthorg.persistence._shared import parse_iso_utc

logger = get_logger(__name__)

_UPSERT_SQL = """\
INSERT INTO project_cost_aggregates
    (project_id, total_cost, total_input_tokens,
     total_output_tokens, record_count, last_updated)
VALUES (?, ?, ?, ?, 1, ?)
ON CONFLICT(project_id) DO UPDATE SET
    total_cost = total_cost + excluded.total_cost,
    total_input_tokens = total_input_tokens + excluded.total_input_tokens,
    total_output_tokens = total_output_tokens + excluded.total_output_tokens,
    record_count = record_count + 1,
    last_updated = excluded.last_updated
RETURNING project_id, total_cost, total_input_tokens,
          total_output_tokens, record_count, last_updated
"""

_SELECT_SQL = """\
SELECT project_id, total_cost, total_input_tokens,
       total_output_tokens, record_count, last_updated
FROM project_cost_aggregates
WHERE project_id = ?
"""

# Module-level guard so the schema-gap notice fires once per process
# regardless of how many ``SQLiteProjectCostAggregateRepository``
# instances are constructed.
_currency_pin_warning_emitted = False


def _emit_currency_pin_construction_warning_once() -> None:
    """Emit the schema-gap notice at most once per process."""
    global _currency_pin_warning_emitted  # noqa: PLW0603
    if _currency_pin_warning_emitted:
        return
    _currency_pin_warning_emitted = True
    logger.info(
        PERSISTENCE_PROJECT_COST_AGG_CURRENCY_PIN_MISSING,
        backend="sqlite",
        note=(
            "project_cost_aggregates schema lacks a 'currency' column;"
            " enforcing same-currency invariant via a process-local"
            " in-memory pin until #1597 adds the durable column."
        ),
    )


def _row_to_aggregate(
    row: aiosqlite.Row,
    *,
    currency: str,
) -> ProjectCostAggregate:
    """Reconstruct a ``ProjectCostAggregate`` from a database row.

    Args:
        row: A single database row.
        currency: Currency to inject before validation since the
            schema does not yet carry a ``currency`` column.

    Returns:
        Validated model instance.

    Raises:
        ValidationError: If the row data fails Pydantic validation.
    """
    data = dict(row)
    data.setdefault("currency", currency)
    # SQLite stores ``last_updated`` as ISO-8601 TEXT (the ``CHECK``
    # constraint enforces ``+00:00`` / ``Z`` suffixes).  Pydantic's
    # ``AwareDatetime`` validator will refuse a string in some modes
    # and the canonical persistence helper rejects naive datetimes
    # outright -- route through ``parse_iso_utc`` so the model gets
    # a real tz-aware ``datetime`` and any timezone violation surfaces
    # as ``ValueError`` here, not as a Pydantic error mid-validation.
    last_updated = data.get("last_updated")
    if isinstance(last_updated, str):
        data["last_updated"] = parse_iso_utc(last_updated)
    return ProjectCostAggregate.model_validate(data)


class SQLiteProjectCostAggregateRepository:
    """SQLite-backed project cost aggregate repository.

    Provides atomic increment and lookup for per-project lifetime
    cost totals.  Uses ``INSERT ... ON CONFLICT DO UPDATE`` for
    atomic upsert semantics.

    The schema currently has no ``currency`` column (#1597 will add
    it).  This repo holds an in-memory ``_pinned_currencies`` map and
    rejects mismatched-currency increments with
    :class:`MixedCurrencyAggregationError`.  The pin is process-local
    and rebuilt on restart -- gaps are logged at WARNING during
    construction so operators are aware.

    Args:
        db: An open aiosqlite connection with ``row_factory``
            set to ``aiosqlite.Row``.
        write_lock: Optional shared write lock for serialising
            multi-statement write operations.
    """

    def __init__(
        self,
        db: aiosqlite.Connection,
        *,
        write_lock: asyncio.Lock | None = None,
    ) -> None:
        self._db = db
        self._write_lock = write_lock if write_lock is not None else asyncio.Lock()
        self._pinned_currencies: dict[str, str] = {}
        # Per-project striped locks: each project_id gets its own
        # ``asyncio.Lock`` lazily created in ``_project_lock``.  This
        # lets concurrent increments to *different* projects run in
        # parallel while still serialising same-project increments
        # (where the same-currency check-and-set + DB I/O must be
        # atomic).  The ``_registry_lock`` only guards the lazy-create
        # path; once a project's lock exists, callers acquire it
        # without going through the registry lock.
        self._lock_registry: dict[str, asyncio.Lock] = {}
        self._registry_lock: asyncio.Lock = asyncio.Lock()
        _emit_currency_pin_construction_warning_once()

    async def _project_lock(self, project_id: str) -> asyncio.Lock:
        """Return the per-project ``asyncio.Lock`` for ``project_id``."""
        existing = self._lock_registry.get(project_id)
        if existing is not None:
            return existing
        async with self._registry_lock:
            existing = self._lock_registry.get(project_id)
            if existing is not None:
                return existing
            lock = asyncio.Lock()
            self._lock_registry[project_id] = lock
            return lock

    async def get(
        self,
        project_id: NotBlankStr,
    ) -> ProjectCostAggregate | None:
        """Retrieve the aggregate for a project.

        Args:
            project_id: Project identifier.

        Returns:
            The aggregate, or ``None`` if no costs recorded.

        Raises:
            QueryError: If the database operation fails.
        """
        try:
            cursor = await self._db.execute(_SELECT_SQL, (project_id,))
            row = await cursor.fetchone()
        except (sqlite3.Error, aiosqlite.Error) as exc:
            logger.warning(
                PERSISTENCE_PROJECT_COST_AGG_FETCH_FAILED,
                project_id=project_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Failed to fetch project cost aggregate for {project_id!r}: {exc}"
            raise QueryError(msg) from exc

        if row is None:
            logger.debug(
                PERSISTENCE_PROJECT_COST_AGG_FETCHED,
                project_id=project_id,
                found=False,
            )
            return None

        # Read the in-memory pinned currency, falling back to
        # DEFAULT_CURRENCY when the process has no record (e.g. after
        # restart; #1597 will move this to a durable column).
        # ``dict.get`` is atomic under the GIL and never yields, so no
        # lock is required on the read path.
        pinned = self._pinned_currencies.get(project_id)
        if pinned is None:
            logger.debug(
                PERSISTENCE_PROJECT_COST_AGG_CURRENCY_PIN_MISSING,
                backend="sqlite",
                project_id=project_id,
                fallback=DEFAULT_CURRENCY,
                note="no in-memory pin for project; using DEFAULT_CURRENCY",
            )
        currency = pinned if pinned is not None else DEFAULT_CURRENCY

        try:
            aggregate = _row_to_aggregate(row, currency=currency)
        except (ValidationError, ValueError) as exc:
            # ``ValueError`` from ``parse_iso_utc`` (naive datetime,
            # malformed ISO string) and ``ValidationError`` from
            # Pydantic both signal a corrupt durable row -- treat
            # uniformly as a deserialization failure.
            logger.warning(
                PERSISTENCE_PROJECT_COST_AGG_DESERIALIZE_FAILED,
                project_id=project_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = (
                f"Failed to deserialize project cost aggregate"
                f" for {project_id!r}: {exc}"
            )
            raise QueryError(msg) from exc

        logger.debug(
            PERSISTENCE_PROJECT_COST_AGG_FETCHED,
            project_id=project_id,
            found=True,
            total_cost=aggregate.total_cost,
            record_count=aggregate.record_count,
        )
        return aggregate

    async def increment(
        self,
        project_id: NotBlankStr,
        cost: float,
        input_tokens: int,
        output_tokens: int,
        *,
        currency: CurrencyCode,
    ) -> ProjectCostAggregate:
        """Atomically increment the project's cost aggregate.

        Creates a new row on first call; increments on subsequent.
        Uses ``RETURNING`` to read back the updated row inside the
        same locked section, avoiding race conditions with concurrent
        increments.

        On the first increment for a project the ``currency`` is
        pinned in memory.  Subsequent increments must match the pin;
        a mismatch raises :class:`MixedCurrencyAggregationError`
        before any DB I/O happens.

        Args:
            project_id: Project identifier.
            cost: Cost delta to add (must be finite and >= 0).
            input_tokens: Input token delta (must be >= 0).
            output_tokens: Output token delta (must be >= 0).
            currency: ISO 4217 currency for ``cost``.

        Returns:
            The updated aggregate after the increment.

        Raises:
            MixedCurrencyAggregationError: If the project already has
                an aggregate row in a different currency.
            QueryError: If the database operation fails.
            ValueError: If any delta is negative or cost is
                non-finite (NaN/Inf).
        """
        if not math.isfinite(cost) or cost < 0 or input_tokens < 0 or output_tokens < 0:
            msg = (
                "Deltas must be finite and non-negative: "
                f"cost={cost}, input_tokens={input_tokens}, "
                f"output_tokens={output_tokens}"
            )
            logger.warning(
                PERSISTENCE_PROJECT_COST_AGG_INCREMENT_FAILED,
                project_id=project_id,
                cost=cost,
                error=msg,
            )
            raise ValueError(msg)

        # Acquire the per-project lock so concurrent increments to
        # *this* project serialise their check-and-set + DB I/O, while
        # concurrent increments to *other* projects can proceed in
        # parallel.  ``_pin_was_set`` tracks whether *this* call wrote
        # the pin so we know whether to roll it back on any failure
        # mode (``aiosqlite.Error`` AND deserialize ``ValidationError``)
        # -- without that, a deserialize failure would leave a phantom
        # pin behind and block future retries.
        project_lock = await self._project_lock(project_id)
        async with project_lock:
            pinned = self._pinned_currencies.get(project_id)
            pin_was_set = False
            if pinned is None:
                self._pinned_currencies[project_id] = currency
                pin_was_set = True
            elif pinned != currency:
                logger.warning(
                    PERSISTENCE_PROJECT_COST_AGG_INCREMENT_FAILED,
                    project_id=project_id,
                    pinned_currency=pinned,
                    incoming_currency=currency,
                    reason="mixed_currency_aggregation",
                )
                msg = (
                    f"Project {project_id!r} aggregate is in {pinned!r}; "
                    f"refusing increment in {currency!r}"
                )
                raise MixedCurrencyAggregationError(
                    msg,
                    currencies=frozenset({pinned, currency}),
                    project_id=project_id,
                )

            now = datetime.now(UTC).isoformat()
            db_committed = False
            try:
                # Run the write inside the write_lock and validate the
                # RETURNING row BEFORE committing.  If deserialize
                # fails the durable layer must remain unchanged so a
                # retry doesn't double-count -- without this guard the
                # commit would land first and the pin rollback below
                # would leave a phantom durable row whose currency the
                # operator can no longer enforce in memory.
                try:
                    async with self._write_lock:
                        cursor = await self._db.execute(
                            _UPSERT_SQL,
                            (project_id, cost, input_tokens, output_tokens, now),
                        )
                        row = await cursor.fetchone()
                        if row is None:  # pragma: no cover -- defensive
                            await self._db.rollback()
                            msg = f"Aggregate for {project_id!r} missing after upsert"
                            raise QueryError(msg)
                        try:
                            aggregate = _row_to_aggregate(row, currency=currency)
                        except (ValidationError, ValueError) as exc:
                            # ``ValueError`` from ``parse_iso_utc`` and
                            # ``ValidationError`` from Pydantic are both
                            # corrupt-row signals -- handle uniformly.
                            await self._db.rollback()
                            logger.warning(
                                PERSISTENCE_PROJECT_COST_AGG_DESERIALIZE_FAILED,
                                project_id=project_id,
                                error_type=type(exc).__name__,
                                error=safe_error_description(exc),
                            )
                            msg = (
                                f"Failed to deserialize project cost aggregate"
                                f" for {project_id!r} after increment: {exc}"
                            )
                            raise QueryError(msg) from exc
                        await self._db.commit()
                except (sqlite3.Error, aiosqlite.Error) as exc:
                    logger.warning(
                        PERSISTENCE_PROJECT_COST_AGG_INCREMENT_FAILED,
                        project_id=project_id,
                        cost=cost,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )
                    msg = (
                        f"Failed to increment project cost aggregate for "
                        f"{project_id!r}: {exc}"
                    )
                    raise QueryError(msg) from exc
                db_committed = True
            finally:
                # Roll back the pin we set on any failure mode --
                # ``aiosqlite.Error`` (DB write failure), missing
                # RETURNING row, or ``ValidationError`` (deserialize
                # failure) all leave the durable layer unchanged
                # (we rollback before re-raise on the deserialize
                # path), so the in-memory pin must follow.
                if pin_was_set and not db_committed:
                    self._pinned_currencies.pop(project_id, None)

        logger.info(
            PERSISTENCE_PROJECT_COST_AGG_INCREMENTED,
            project_id=project_id,
            cost_delta=cost,
            currency=currency,
            total_cost=aggregate.total_cost,
            record_count=aggregate.record_count,
        )
        return aggregate
