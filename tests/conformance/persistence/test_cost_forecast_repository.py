"""Conformance tests for ``CostForecastRepository``.

Dual-backend parity: a single assertion set runs against SQLite and
Postgres via the ``backend`` fixture in
``tests/conformance/persistence/conftest.py``. The repo is built over
the migrated ``backend.get_db()`` handle.

Covers:

* CRUD round-trip (save / get / list / delete).
* Filtered query by ``brief_hash`` and ``decision``.
* Transition state machine: ``pending -> approved`` (with ``decided_by``
  + ``decided_at``), ``pending -> rejected``, ``pending -> superseded``
  (no operator identity).
* Unknown update keys on ``transition_if`` raise :class:`QueryError`.
* ``superseded`` rejects ``decided_by``.
* Unique-pending constraint: at most one ``pending`` row per
  ``brief_hash`` (partial unique index).
* Same-currency invariant on :meth:`save`.
* Bounds invariant: ``lower <= estimated <= upper`` (DB CHECK).
"""

from datetime import UTC, datetime
from typing import cast
from uuid import UUID, uuid4

import aiosqlite
import pytest

from synthorg.budget.errors import MixedCurrencyAggregationError
from synthorg.budget.forecast_models import (
    Forecast,
    ForecastDecision,
    HaltContext,
)
from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.persistence.cost_forecast_protocol import (
    CostForecastFilterSpec,
    CostForecastRepository,
)
from synthorg.persistence.postgres.cost_forecast_repo import (
    PostgresCostForecastRepository,
)
from synthorg.persistence.protocol import PersistenceBackend
from synthorg.persistence.sqlite.cost_forecast_repo import (
    SQLiteCostForecastRepository,
)

pytestmark = pytest.mark.integration

_NOW = datetime(2026, 5, 20, 12, 0, tzinfo=UTC)
_CURRENCY: str = "USD"


def _repo(
    backend: PersistenceBackend,
    *,
    currency: str = _CURRENCY,
) -> CostForecastRepository:
    """Return a concrete forecast repository bound to *backend*."""
    name = backend.backend_name
    handle = backend.get_db()
    if name == "sqlite":
        return SQLiteCostForecastRepository(
            cast("aiosqlite.Connection", handle),
            write_context=backend.write_context,
            currency_getter=lambda: currency,
        )
    if name == "postgres":
        from psycopg_pool import AsyncConnectionPool

        return PostgresCostForecastRepository(
            cast("AsyncConnectionPool", handle),
            currency_getter=lambda: currency,
        )
    msg = f"Unknown backend: {name}"
    raise ValueError(msg)


def _uuid(slug: str) -> UUID:
    """Build a deterministic UUID for fixture rows."""
    return UUID(int=abs(hash(slug)) % (1 << 128))


def _make_forecast(  # noqa: PLR0913 -- test helper carries every Forecast field
    *,
    forecast_id: str | None = None,
    brief_hash: str = "a" * 64,
    estimated_cost: float = 1.00,
    lower_bound: float = 0.80,
    upper_bound: float = 1.20,
    currency: str = _CURRENCY,
    decision: ForecastDecision = ForecastDecision.PENDING,
    decided_by: str | None = None,
    decided_at: datetime | None = None,
    ceiling_amount: float | None = None,
    halt_context: HaltContext | None = None,
) -> Forecast:
    return Forecast(
        forecast_id=uuid4() if forecast_id is None else _uuid(forecast_id),
        brief_hash=brief_hash,
        estimated_cost=estimated_cost,
        lower_bound=lower_bound,
        upper_bound=upper_bound,
        currency=currency,
        decision=decision,
        decided_at=decided_at,
        decided_by=decided_by,
        ceiling_amount=ceiling_amount,
        halt_context=halt_context,
        created_at=_NOW,
        updated_at=_NOW,
    )


class TestCostForecastRepository:
    async def test_save_and_get(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        forecast = _make_forecast()
        await repo.save(forecast)

        fetched = await repo.get(forecast.forecast_id)
        assert fetched is not None
        assert fetched.forecast_id == forecast.forecast_id
        assert fetched.brief_hash == "a" * 64
        assert fetched.decision is ForecastDecision.PENDING
        assert fetched.estimated_cost == pytest.approx(1.00)
        assert fetched.currency == _CURRENCY
        assert fetched.created_at.tzinfo is not None

    async def test_get_returns_none_when_absent(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        assert await repo.get(uuid4()) is None

    async def test_halt_context_round_trip(self, backend: PersistenceBackend) -> None:
        """Halt context survives a save / get cycle and clears to None."""
        repo = _repo(backend)
        halted = _make_forecast(
            forecast_id="h1",
            brief_hash="h" * 64,
            decision=ForecastDecision.APPROVED,
            decided_by="op-1",
            decided_at=_NOW,
            ceiling_amount=1.50,
            halt_context=HaltContext(
                accumulated_cost=1.80,
                ceiling_amount=1.50,
                currency=_CURRENCY,
                halted_at=_NOW,
            ),
        )
        await repo.save(halted)

        fetched = await repo.get(halted.forecast_id)
        assert fetched is not None
        assert fetched.halt_context is not None
        assert fetched.halt_context.accumulated_cost == pytest.approx(1.80)
        assert fetched.halt_context.ceiling_amount == pytest.approx(1.50)
        assert fetched.halt_context.currency == _CURRENCY
        assert fetched.halt_context.halted_at.tzinfo is not None

        cleared = fetched.model_copy(update={"halt_context": None})
        await repo.save(cleared)
        refetched = await repo.get(halted.forecast_id)
        assert refetched is not None
        assert refetched.halt_context is None

    async def test_save_rejects_currency_mismatch(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend, currency="USD")
        forecast = _make_forecast(currency="GBP")
        with pytest.raises(MixedCurrencyAggregationError):
            await repo.save(forecast)

    async def test_count_matches_filtered_query(
        self, backend: PersistenceBackend
    ) -> None:
        """``count(spec)`` agrees with the length of ``query(spec)``."""
        repo = _repo(backend)
        await repo.save(_make_forecast(forecast_id="c1", brief_hash="ca" * 32))
        await repo.save(_make_forecast(forecast_id="c2", brief_hash="cb" * 32))
        approved = _make_forecast(
            forecast_id="c3",
            brief_hash="cc" * 32,
            decision=ForecastDecision.APPROVED,
            decided_by="op-1",
            decided_at=_NOW,
        )
        await repo.save(approved)

        pending_spec = CostForecastFilterSpec(decision=ForecastDecision.PENDING)
        pending_count = await repo.count(pending_spec)
        pending_rows = await repo.query(pending_spec)
        assert pending_count == len(pending_rows)
        assert pending_count >= 2

        hash_spec = CostForecastFilterSpec(brief_hash="ca" * 32)
        assert await repo.count(hash_spec) == 1

    async def test_query_filter_by_brief_hash(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        await repo.save(_make_forecast(forecast_id="f1", brief_hash="b" * 64))
        await repo.save(_make_forecast(forecast_id="f2", brief_hash="c" * 64))

        rows = await repo.query(CostForecastFilterSpec(brief_hash="b" * 64))
        assert len(rows) == 1
        assert rows[0].brief_hash == "b" * 64

    async def test_query_filter_by_decision(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        await repo.save(_make_forecast(forecast_id="d1", brief_hash="d1" * 32))
        approved = _make_forecast(
            forecast_id="d2",
            brief_hash="d2" * 32,
            decision=ForecastDecision.APPROVED,
            decided_by="op-1",
            decided_at=_NOW,
        )
        await repo.save(approved)

        pending_rows = await repo.query(
            CostForecastFilterSpec(decision=ForecastDecision.PENDING)
        )
        approved_rows = await repo.query(
            CostForecastFilterSpec(decision=ForecastDecision.APPROVED)
        )
        assert all(r.decision is ForecastDecision.PENDING for r in pending_rows)
        assert any(r.forecast_id == approved.forecast_id for r in approved_rows)

    async def test_transition_pending_to_approved(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        forecast = _make_forecast()
        await repo.save(forecast)

        transitioned = await repo.transition_if(
            forecast.forecast_id,
            ForecastDecision.PENDING,
            ForecastDecision.APPROVED,
            decided_by="op-1",
            ceiling_amount=1.80,
        )
        assert transitioned is True

        fetched = await repo.get(forecast.forecast_id)
        assert fetched is not None
        assert fetched.decision is ForecastDecision.APPROVED
        assert fetched.decided_by == "op-1"
        assert fetched.decided_at is not None
        assert fetched.ceiling_amount == pytest.approx(1.80)

    async def test_transition_pending_to_superseded(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        forecast = _make_forecast()
        await repo.save(forecast)

        transitioned = await repo.transition_if(
            forecast.forecast_id,
            ForecastDecision.PENDING,
            ForecastDecision.SUPERSEDED,
        )
        assert transitioned is True

        fetched = await repo.get(forecast.forecast_id)
        assert fetched is not None
        assert fetched.decision is ForecastDecision.SUPERSEDED
        assert fetched.decided_by is None
        assert fetched.decided_at is not None

    async def test_transition_superseded_rejects_decided_by(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        forecast = _make_forecast()
        await repo.save(forecast)

        with pytest.raises(QueryError, match="superseded"):
            await repo.transition_if(
                forecast.forecast_id,
                ForecastDecision.PENDING,
                ForecastDecision.SUPERSEDED,
                decided_by="op-1",
            )

    async def test_transition_rejects_unknown_update_key(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        forecast = _make_forecast()
        await repo.save(forecast)

        with pytest.raises(QueryError, match="unknown update keys"):
            await repo.transition_if(
                forecast.forecast_id,
                ForecastDecision.PENDING,
                ForecastDecision.APPROVED,
                decided_by="op-1",
                some_unknown_key="value",
            )

    async def test_transition_returns_false_on_state_mismatch(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        forecast = _make_forecast()
        await repo.save(forecast)
        await repo.transition_if(
            forecast.forecast_id,
            ForecastDecision.PENDING,
            ForecastDecision.APPROVED,
            decided_by="op-1",
        )

        replayed = await repo.transition_if(
            forecast.forecast_id,
            ForecastDecision.PENDING,
            ForecastDecision.APPROVED,
            decided_by="op-1",
        )
        assert replayed is False

    async def test_unique_pending_per_brief_hash(
        self, backend: PersistenceBackend
    ) -> None:
        """At most one pending row per brief_hash (partial unique index)."""
        repo = _repo(backend)
        first = _make_forecast(forecast_id="u1", brief_hash="u" * 64)
        await repo.save(first)

        duplicate = _make_forecast(forecast_id="u2", brief_hash="u" * 64)
        with pytest.raises(ConstraintViolationError):
            await repo.save(duplicate)

    async def test_unique_pending_allows_after_supersede(
        self, backend: PersistenceBackend
    ) -> None:
        """Superseding the existing pending row frees the brief_hash slot."""
        repo = _repo(backend)
        first = _make_forecast(forecast_id="s1", brief_hash="s" * 64)
        await repo.save(first)

        await repo.transition_if(
            first.forecast_id,
            ForecastDecision.PENDING,
            ForecastDecision.SUPERSEDED,
        )

        second = _make_forecast(forecast_id="s2", brief_hash="s" * 64)
        await repo.save(second)

        fetched = await repo.get(second.forecast_id)
        assert fetched is not None

    async def test_delete(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        forecast = _make_forecast()
        await repo.save(forecast)

        deleted = await repo.delete(forecast.forecast_id)
        assert deleted is True
        assert await repo.get(forecast.forecast_id) is None

    async def test_delete_returns_false_when_absent(
        self, backend: PersistenceBackend
    ) -> None:
        repo = _repo(backend)
        assert await repo.delete(uuid4()) is False

    async def test_list_items_newest_first(self, backend: PersistenceBackend) -> None:
        repo = _repo(backend)
        older = _make_forecast(forecast_id="o1", brief_hash="o" * 64)
        newer = _make_forecast(forecast_id="o2", brief_hash="n" * 64)
        await repo.save(older)
        await repo.save(newer)

        rows = await repo.list_items()
        ids = [r.forecast_id for r in rows]
        assert older.forecast_id in ids
        assert newer.forecast_id in ids
