"""Postgres implementation of the ProjectCostAggregateRepository protocol.

This is the Postgres sibling of
src/synthorg/persistence/sqlite/project_cost_aggregate_repo.py.
Postgres stores total_cost and token counts as native numeric types.

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
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import psycopg
from psycopg.rows import dict_row
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

if TYPE_CHECKING:
    from psycopg_pool import AsyncConnectionPool

logger = get_logger(__name__)

_UPSERT_SQL = """\
INSERT INTO project_cost_aggregates
    (project_id, total_cost, total_input_tokens,
     total_output_tokens, record_count, last_updated)
VALUES (%s, %s, %s, %s, 1, %s)
ON CONFLICT(project_id) DO UPDATE SET
    total_cost = project_cost_aggregates.total_cost + EXCLUDED.total_cost,
    total_input_tokens = project_cost_aggregates.total_input_tokens
        + EXCLUDED.total_input_tokens,
    total_output_tokens = project_cost_aggregates.total_output_tokens
        + EXCLUDED.total_output_tokens,
    record_count = project_cost_aggregates.record_count + 1,
    last_updated = EXCLUDED.last_updated
RETURNING project_id, total_cost, total_input_tokens,
          total_output_tokens, record_count, last_updated
"""

_SELECT_SQL = """\
SELECT project_id, total_cost, total_input_tokens,
       total_output_tokens, record_count, last_updated
FROM project_cost_aggregates
WHERE project_id = %s
"""

# Module-level guard so the schema-gap warning fires once per process
# regardless of how many ``PostgresProjectCostAggregateRepository``
# instances are constructed (test suites typically build one per test).
_currency_pin_warning_emitted = False


def _emit_currency_pin_construction_warning_once() -> None:
    """Emit the schema-gap notice at most once per process."""
    global _currency_pin_warning_emitted  # noqa: PLW0603
    if _currency_pin_warning_emitted:
        return
    _currency_pin_warning_emitted = True
    logger.info(
        PERSISTENCE_PROJECT_COST_AGG_CURRENCY_PIN_MISSING,
        backend="postgres",
        note=(
            "project_cost_aggregates schema lacks a 'currency' column;"
            " enforcing same-currency invariant via a process-local"
            " in-memory pin until #1597 adds the durable column."
        ),
    )


class PostgresProjectCostAggregateRepository:
    """Postgres-backed project cost aggregate repository.

    Provides atomic increment and lookup for per-project lifetime
    cost totals.  Uses ``INSERT ... ON CONFLICT DO UPDATE`` for
    atomic upsert semantics.

    The schema currently has no ``currency`` column (#1597 will add
    it).  This repo holds an in-memory ``_pinned_currencies`` map and
    rejects mismatched-currency increments with
    :class:`MixedCurrencyAggregationError`.  The pin is process-local
    and rebuilt on restart -- gaps are logged at WARNING during
    construction and on first increment so operators are aware.

    Args:
        pool: An open psycopg_pool.AsyncConnectionPool.
    """

    def __init__(self, pool: AsyncConnectionPool) -> None:
        self._pool = pool
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

    @staticmethod
    def _deserialize(
        row: dict[str, object],
        project_id: NotBlankStr,
        *,
        context: str = "",
    ) -> ProjectCostAggregate:
        """Validate a raw row into a ``ProjectCostAggregate``.

        Centralizes the Pydantic validation + ``QueryError`` wrap used
        by both ``get()`` and ``increment()`` so the logging/event
        constant stays consistent.

        Args:
            row: Raw mapping returned by psycopg.
            project_id: Project id (for error context + logging).
            context: Optional suffix describing the call site
                (e.g. ``"after increment"``).

        Raises:
            QueryError: If the row cannot be validated.
        """
        try:
            return ProjectCostAggregate.model_validate(row)
        except ValidationError as exc:
            logger.warning(
                PERSISTENCE_PROJECT_COST_AGG_DESERIALIZE_FAILED,
                project_id=project_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            suffix = f" {context}" if context else ""
            msg = (
                f"Failed to deserialize project cost aggregate"
                f" for {project_id!r}{suffix}: {safe_error_description(exc)}"
            )
            raise QueryError(msg) from exc

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
            async with (
                self._pool.connection() as conn,
                conn.cursor(row_factory=dict_row) as cur,
            ):
                await cur.execute(_SELECT_SQL, (project_id,))
                row = await cur.fetchone()
        except psycopg.Error as exc:
            logger.warning(
                PERSISTENCE_PROJECT_COST_AGG_FETCH_FAILED,
                project_id=project_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = f"Failed to fetch project cost aggregate for {project_id!r}: {safe_error_description(exc)}"  # noqa: E501
            raise QueryError(msg) from exc

        if row is None:
            logger.debug(
                PERSISTENCE_PROJECT_COST_AGG_FETCHED,
                project_id=project_id,
                found=False,
            )
            return None

        # Inject the in-memory pinned currency before validation since
        # the table column does not yet exist. When a process
        # observes a project for the first time after restart, no pin
        # exists yet -- fall back to ``DEFAULT_CURRENCY`` so validation
        # succeeds; the next ``increment`` call carries the operator's
        # actual currency and re-establishes the pin authoritatively.
        # ``dict.get`` is atomic under the GIL and never yields, so no
        # lock is required on the read path -- holding one would
        # serialise every read for no safety benefit.
        pinned = self._pinned_currencies.get(project_id)
        if pinned is None:
            logger.debug(
                PERSISTENCE_PROJECT_COST_AGG_CURRENCY_PIN_MISSING,
                backend="postgres",
                project_id=project_id,
                fallback=DEFAULT_CURRENCY,
                note="no in-memory pin for project; using DEFAULT_CURRENCY",
            )
        row.setdefault("currency", pinned if pinned is not None else DEFAULT_CURRENCY)

        aggregate = self._deserialize(row, project_id)

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
        same transaction, avoiding race conditions with concurrent
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
        # mode (``psycopg.Error`` AND ``QueryError`` from
        # ``_deserialize``); without that, a deserialize failure would
        # leave a phantom pin behind and block future retries.
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

            now = datetime.now(UTC)
            db_committed = False
            try:
                try:
                    async with (
                        self._pool.connection() as conn,
                        conn.cursor(row_factory=dict_row) as cur,
                    ):
                        await cur.execute(
                            _UPSERT_SQL,
                            (project_id, cost, input_tokens, output_tokens, now),
                        )
                        row = await cur.fetchone()
                        if row is None:  # pragma: no cover -- defensive
                            logger.error(
                                PERSISTENCE_PROJECT_COST_AGG_INCREMENT_FAILED,
                                project_id=project_id,
                                error=("RETURNING clause produced no row after upsert"),
                            )
                            await conn.rollback()
                            msg = f"Aggregate for {project_id!r} missing after upsert"
                            raise QueryError(msg)
                        # Inject the in-memory pinned currency before
                        # validation since the table column does not
                        # yet exist.
                        row.setdefault("currency", currency)
                        try:
                            aggregate = self._deserialize(
                                row, project_id, context="after increment"
                            )
                        except QueryError:
                            await conn.rollback()
                            raise
                        await conn.commit()
                except psycopg.Error as exc:
                    logger.warning(
                        PERSISTENCE_PROJECT_COST_AGG_INCREMENT_FAILED,
                        project_id=project_id,
                        cost=cost,
                        error_type=type(exc).__name__,
                        error=safe_error_description(exc),
                    )
                    msg = (
                        f"Failed to increment project cost aggregate for "
                        f"{project_id!r}: {safe_error_description(exc)}"
                    )
                    raise QueryError(msg) from exc
                db_committed = True
            finally:
                # Roll back the pin we set on any failure mode --
                # ``psycopg.Error`` (DB write/connect failure) AND
                # ``QueryError`` (deserialize failure or missing
                # RETURNING row) both leave the durable layer
                # unchanged, so the in-memory pin must follow.
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
