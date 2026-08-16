"""A refusal that does not clear without an operator survives a restart.

The verdict states its own persistence requirement in its own text: *this
does not clear without an operator*. A process restart is not an operator,
and the outcomes the latch is read from live in memory, so a restart used to
clear it: the agent stood down for an empty balance was offered the same work
on the same refusing pair minutes later, and the operator who saw the warning
had no way to know it had ever been raised.

These exercise the two halves of the fix against a fake store: what is
written when a pair refuses, and what a fresh tracker reads back.
"""

from datetime import UTC, datetime, timedelta

import pytest

from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr
from synthorg.providers.agent_availability import unavailability_from
from synthorg.providers.health import (
    HEALTH_WINDOW_HOURS,
    ProviderHealthRecord,
    ProviderHealthStatus,
    ProviderOutcomeClass,
    RecordSource,
)
from synthorg.providers.health_tracker import ProviderHealthTracker
from synthorg.providers.latch import LatchedFailure

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
_PROVIDER = "example-provider"
_MODEL = "example-expert-001"


class _FakeLatchStore:
    """In-process stand-in for the durable repository.

    A hand-written double rather than a mock: these tests care about what
    ends up stored across two tracker lifetimes, which is state, and a call
    recorder would assert on the calls instead of on the outcome.
    """

    def __init__(
        self,
        *,
        fail_on_save: bool = False,
        fail_on_list: bool = False,
        fail_on_purge: bool = False,
    ) -> None:
        self.rows: dict[tuple[str, str], LatchedFailure] = {}
        self.fail_on_save = fail_on_save
        self.fail_on_list = fail_on_list
        self.fail_on_purge = fail_on_purge

    async def save(self, entity: LatchedFailure, /) -> None:
        if self.fail_on_save:
            msg = "database down"
            raise QueryError(msg)
        stored = self.rows.get(entity.pair)
        # Mirrors the repositories' monotonic upsert guard, so a test that
        # replays an older record cannot pass here and fail against SQL.
        if stored is not None and entity.occurred_at < stored.occurred_at:
            return
        self.rows[entity.pair] = entity

    async def get(self, entity_id: tuple[str, str], /) -> LatchedFailure | None:
        return self.rows.get(entity_id)

    async def delete(self, entity_id: tuple[str, str], /) -> bool:
        return self.rows.pop(entity_id, None) is not None

    async def list_items(
        self, *, limit: int = 50, offset: int = 0
    ) -> tuple[LatchedFailure, ...]:
        if self.fail_on_list:
            msg = "database down"
            raise QueryError(msg)
        ordered = [self.rows[key] for key in sorted(self.rows)]
        return tuple(ordered[offset : offset + limit])

    async def purge_before(self, threshold: datetime, /) -> int:
        if self.fail_on_purge:
            msg = "database down"
            raise QueryError(msg)
        expired = [key for key, row in self.rows.items() if row.occurred_at < threshold]
        for key in expired:
            del self.rows[key]
        return len(expired)


def _refusal(
    *,
    at: datetime = _NOW,
    outcome: ProviderOutcomeClass = ProviderOutcomeClass.PAYMENT_REQUIRED,
    model: str | None = _MODEL,
    source: RecordSource = RecordSource.REAL_CALL,
) -> ProviderHealthRecord:
    return ProviderHealthRecord(
        provider_name=NotBlankStr(_PROVIDER),
        model=None if model is None else NotBlankStr(model),
        timestamp=at,
        success=False,
        outcome_class=outcome,
        response_time_ms=311.0,
        error_message=NotBlankStr("insufficient balance"),
        source=source,
    )


def _tracker(store: _FakeLatchStore | None) -> ProviderHealthTracker:
    tracker = ProviderHealthTracker()
    if store is not None:
        tracker.bind_latch_store(store)
    return tracker


class TestWriteThrough:
    async def test_a_refusal_is_written_through(self) -> None:
        store = _FakeLatchStore()
        await _tracker(store).record(_refusal())

        stored = store.rows[_PROVIDER, _MODEL]
        assert stored.outcome_class is ProviderOutcomeClass.PAYMENT_REQUIRED
        assert stored.occurred_at == _NOW

    @pytest.mark.parametrize(
        ("kwargs", "why"),
        [
            ({"outcome": ProviderOutcomeClass.OVERLOADED}, "does not latch"),
            ({"model": None}, "names no pair"),
            ({"source": RecordSource.PROBE}, "is not a real call"),
        ],
    )
    async def test_an_outcome_that_does_not_latch_stores_nothing(
        self, kwargs: dict[str, object], why: str
    ) -> None:
        # The store must not accumulate rows the reader would never honour;
        # the conditions here are the reader's own, restated nowhere else.
        store = _FakeLatchStore()
        await _tracker(store).record(_refusal(**kwargs))  # type: ignore[arg-type]

        assert store.rows == {}, why

    async def test_a_failed_write_leaves_the_in_memory_latch_standing(self) -> None:
        # The pair is out either way; what is lost is the next restart.
        store = _FakeLatchStore(fail_on_save=True)
        tracker = _tracker(store)

        await tracker.record(_refusal())

        view = await tracker.get_serviceability(_PROVIDER, _MODEL, now=_NOW)
        assert view.verdict is ProviderHealthStatus.DOWN

    async def test_no_store_bound_records_exactly_as_before(self) -> None:
        tracker = _tracker(None)
        await tracker.record(_refusal())

        view = await tracker.get_serviceability(_PROVIDER, _MODEL, now=_NOW)
        assert view.has_latching_failure is True


class TestRestore:
    async def test_the_pair_is_still_unavailable_after_a_restart(self) -> None:
        store = _FakeLatchStore()
        await _tracker(store).record(_refusal())

        # A different tracker, as a restarted process has: nothing shared but
        # the store.
        restarted = _tracker(store)
        assert await restarted.restore_latches(now=_NOW + timedelta(minutes=5)) == 1

        view = await restarted.get_serviceability(
            _PROVIDER, _MODEL, now=_NOW + timedelta(minutes=5)
        )
        reason = unavailability_from(view)
        assert reason is not None
        assert reason.needs_operator is True
        assert reason.outcome_class is ProviderOutcomeClass.PAYMENT_REQUIRED
        assert reason.since == _NOW

    async def test_a_latch_the_lookback_released_is_dropped(self) -> None:
        store = _FakeLatchStore()
        await _tracker(store).record(_refusal())

        # Past the record window the lookback cannot honour it any more, so
        # the row is released rather than restored: the retry-after fires.
        restarted = _tracker(store)
        later = _NOW + timedelta(hours=HEALTH_WINDOW_HOURS, minutes=1)
        assert await restarted.restore_latches(now=later) == 0
        assert store.rows == {}

        view = await restarted.get_serviceability(_PROVIDER, _MODEL, now=later)
        assert view.has_latching_failure is False

    async def test_a_latch_exactly_at_the_cutoff_is_still_standing(self) -> None:
        """The lookback is inclusive, and the boundary is where it shows.

        A row on the cutoff is the oldest one the lookback can still honour,
        so releasing it here would clear a pair the reader would otherwise
        have kept out of service.
        """
        store = _FakeLatchStore()
        await _tracker(store).record(_refusal())

        restarted = _tracker(store)
        on_the_cutoff = _NOW + timedelta(hours=HEALTH_WINDOW_HOURS)
        assert await restarted.restore_latches(now=on_the_cutoff) == 1
        assert store.rows[_PROVIDER, _MODEL].occurred_at == _NOW

    async def test_an_unreadable_store_is_raised_not_reported_as_empty(self) -> None:
        """The two answers differ by the whole point of the store.

        Degrading a failed read to "nothing latched" is the false clear this
        module exists to prevent, arrived at by a different route; the boot
        wiring declines its subsystem on the raise.
        """
        store = _FakeLatchStore(fail_on_list=True)

        with pytest.raises(QueryError):
            await _tracker(store).restore_latches(now=_NOW)

    async def test_a_failed_release_still_restores_what_is_standing(self) -> None:
        # The asymmetry with the read above is deliberate: an undeleted row is
        # read again next boot and found expired again, so failing the boot
        # over it would cost more than the stale row does.
        store = _FakeLatchStore(fail_on_purge=True)
        await _tracker(store).record(_refusal())
        await _tracker(store).record(
            _refusal(at=_NOW - timedelta(hours=HEALTH_WINDOW_HOURS * 2))
        )

        restarted = _tracker(store)
        assert await restarted.restore_latches(now=_NOW + timedelta(minutes=1)) == 1
        # The expired row is still there, and still expired.
        assert store.rows[_PROVIDER, _MODEL].occurred_at == _NOW

    async def test_restoring_without_a_store_reports_nothing_restored(self) -> None:
        assert await _tracker(None).restore_latches(now=_NOW) == 0

    async def test_a_restored_latch_is_written_through_again(self) -> None:
        # Restoring goes through ``record``, so a second restart keeps the
        # row rather than replacing it with an ageing copy nobody refreshed.
        store = _FakeLatchStore()
        await _tracker(store).record(_refusal())
        await _tracker(store).restore_latches(now=_NOW + timedelta(minutes=1))

        assert store.rows[_PROVIDER, _MODEL].occurred_at == _NOW
