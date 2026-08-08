"""C2: spend is durably recorded, and a restart reads it back.

``cost_records`` had a table, a repository, four indexes and a convention
gate policing its owner columns, and no writer anywhere in the product: the
tracker appended to memory and incremented the project aggregate, so a
ceiling could not survive a restart and every deliverable receipt reported
zero.
"""

from datetime import UTC, datetime, timedelta

import pytest
import structlog

from synthorg.budget.cost_record import CostRecord
from synthorg.budget.currency import CurrencyCode
from synthorg.budget.tracker import CostTracker
from synthorg.core.pagination import MAX_LIST_LIMIT
from synthorg.core.persistence_errors import ConstraintViolationError, QueryError
from synthorg.core.types import NotBlankStr
from synthorg.persistence.cost_record_protocol import (
    CostRecordFilterSpec,
    CostRecordRepository,
)
from synthorg.persistence.project_cost_aggregate_protocol import (
    ProjectCostAggregateRepository,
)
from synthorg.persistence.project_cost_claim_seen_protocol import (
    ProjectCostClaimSeenRepository,
)
from tests._shared import FakeClock, mock_of

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 8, 8, 12, 0, tzinfo=UTC)


class _RecordingRepo:
    """In-memory stand-in for the durable cost-record store.

    ``query`` reproduces the two behaviours of the real repositories that
    hydration has to survive: the newest-first page order, and the silent
    clamp of ``limit`` to ``MAX_LIST_LIMIT`` that
    ``validate_pagination_args`` applies. A fake that honoured a
    1_000_000-row request would make a hydration bug invisible here.
    """

    def __init__(self) -> None:
        self.records: list[CostRecord] = []
        self.pages_requested: list[tuple[int, int]] = []

    async def append(self, record: CostRecord) -> None:
        self.records.append(record)

    async def query(
        self,
        filter_spec: CostRecordFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[CostRecord, ...]:
        self.pages_requested.append((limit, offset))
        limit = min(limit, MAX_LIST_LIMIT)
        rows = self.records
        if filter_spec.since is not None:
            rows = [r for r in rows if r.timestamp >= filter_spec.since]
        rows = sorted(rows, key=lambda r: r.timestamp, reverse=True)
        return tuple(rows[offset : offset + limit])

    async def aggregate(
        self,
        *,
        agent_id: NotBlankStr | None = None,
        task_id: NotBlankStr | None = None,
    ) -> float:
        rows = self.records
        if agent_id is not None:
            rows = [r for r in rows if r.agent_id == agent_id]
        if task_id is not None:
            rows = [r for r in rows if r.task_id == task_id]
        return sum(r.cost for r in rows)

    async def purge_before(self, threshold: datetime) -> int:
        kept = [r for r in self.records if r.timestamp >= threshold]
        removed = len(self.records) - len(kept)
        self.records = kept
        return removed


def _attach(tracker: CostTracker, repo: CostRecordRepository) -> None:
    """Wire the durable trio the way the boot sequence does."""
    tracker.attach_durable_repos(
        project_cost_repo=mock_of[ProjectCostAggregateRepository](),
        claim_seen_repo=mock_of[ProjectCostClaimSeenRepository](),
        cost_record_repo=repo,
    )


def _record(cost: float, *, at: datetime | None = None) -> CostRecord:
    return CostRecord(
        provider=NotBlankStr("example-provider"),
        model=NotBlankStr("example-medium-001"),
        input_tokens=10,
        output_tokens=20,
        cost=cost,
        currency=CurrencyCode("EUR"),
        timestamp=at or _NOW,
    )


class TestDurableAppend:
    async def test_every_accepted_record_reaches_the_durable_store(self) -> None:
        """A ceiling reads the window; a window nothing persisted is a zero."""
        repo = _RecordingRepo()
        tracker = CostTracker(clock=FakeClock(start=_NOW))
        _attach(tracker, repo)

        await tracker.record(_record(0.25))
        await tracker.record(_record(0.5))

        assert [r.cost for r in repo.records] == [0.25, 0.5]

    async def test_a_store_failure_does_not_lose_the_in_memory_window(self) -> None:
        """The run must keep enforcing its ceiling when the store is down."""
        broken = mock_of[CostRecordRepository]()
        broken.append.side_effect = QueryError("store down")
        tracker = CostTracker(clock=FakeClock(start=_NOW))
        _attach(tracker, broken)

        with structlog.testing.capture_logs() as captured:
            await tracker.record(_record(0.25))

        assert await tracker.get_total_cost() == pytest.approx(0.25)
        # The drop is announced, not merely survived: a window that only this
        # process holds is spend nothing else can see, and silence about it is
        # the state C2 exists to end.
        dropped = [e for e in captured if e["event"] == "budget.record.persist_failed"]
        assert len(dropped) == 1
        assert dropped[0]["log_level"] == "warning"

    async def test_a_transient_store_failure_is_retried_before_it_is_dropped(
        self,
    ) -> None:
        """The record is what a restart rehydrates from; one blip must not cost it."""
        flaky = mock_of[CostRecordRepository]()
        attempts = 0

        async def _append(record: CostRecord) -> None:
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                msg = "connection reset"
                raise QueryError(msg)

        flaky.append.side_effect = _append
        tracker = CostTracker(clock=FakeClock(start=_NOW))
        _attach(tracker, flaky)

        with structlog.testing.capture_logs() as captured:
            await tracker.record(_record(0.25))

        assert attempts == 2
        assert not [e for e in captured if e["event"] == "budget.record.persist_failed"]

    async def test_a_run_of_drops_says_spend_is_not_being_recorded(self) -> None:
        """One dropped receipt is a gap; a streak is an unenforceable ceiling."""
        broken = mock_of[CostRecordRepository]()
        broken.append.side_effect = ConstraintViolationError(
            "duplicate", constraint="idx_cost_records_claim_id"
        )
        tracker = CostTracker(clock=FakeClock(start=_NOW))
        _attach(tracker, broken)

        with structlog.testing.capture_logs() as captured:
            for cost in (0.1, 0.2, 0.3):
                await tracker.record(_record(cost))

        dropped = [e for e in captured if e["event"] == "budget.record.persist_failed"]
        assert [e["log_level"] for e in dropped] == ["warning", "warning", "error"]
        assert [e["spend_recorded"] for e in dropped] == [True, True, False]


class TestHydration:
    async def test_a_restart_reads_the_window_back(self) -> None:
        """Spend that survives the process is what makes a ceiling enforceable."""
        repo = _RecordingRepo()
        repo.records.extend([_record(0.25), _record(0.5)])
        tracker = CostTracker(clock=FakeClock(start=_NOW))
        _attach(tracker, repo)

        restored = await tracker.hydrate_from_durable()

        assert restored == 2
        assert await tracker.get_total_cost() == pytest.approx(0.75)

    async def test_hydration_is_bounded_by_the_spend_window(self) -> None:
        """The window is what a ceiling is enforced over, not all of history."""
        repo = _RecordingRepo()
        repo.records.append(_record(9.0, at=_NOW - timedelta(days=30)))
        repo.records.append(_record(0.25))
        tracker = CostTracker(clock=FakeClock(start=_NOW))
        _attach(tracker, repo)

        restored = await tracker.hydrate_from_durable()

        assert restored == 1
        assert await tracker.get_total_cost() == pytest.approx(0.25)

    async def test_no_durable_store_hydrates_nothing(self) -> None:
        """A construction-phase tracker has no backend; it must not raise."""
        tracker = CostTracker(clock=FakeClock(start=_NOW))

        assert await tracker.hydrate_from_durable() == 0

    async def test_hydration_never_asks_for_a_page_the_backend_will_clamp(
        self,
    ) -> None:
        """A single oversized read is a silently truncated window.

        Every repository funnels ``limit`` through ``validate_pagination_args``,
        which clamps to ``MAX_LIST_LIMIT`` without telling the caller, so a
        request for the whole window comes back as its newest page and the
        ceiling is enforced over a fraction of the spend.
        """
        repo = _RecordingRepo()
        repo.records.extend([_record(0.25), _record(0.5)])
        tracker = CostTracker(clock=FakeClock(start=_NOW))
        _attach(tracker, repo)

        await tracker.hydrate_from_durable()

        assert repo.pages_requested
        assert all(limit <= MAX_LIST_LIMIT for limit, _ in repo.pages_requested)

    async def test_hydration_pages_past_the_backend_cap(self) -> None:
        """The window is 168 hours of spend, not one page of it."""
        repo = _RecordingRepo()
        repo.records.extend(
            _record(0.01, at=_NOW - timedelta(seconds=i))
            for i in range(MAX_LIST_LIMIT + 1)
        )
        tracker = CostTracker(clock=FakeClock(start=_NOW))
        _attach(tracker, repo)

        restored = await tracker.hydrate_from_durable()

        assert restored == MAX_LIST_LIMIT + 1
        assert len(repo.pages_requested) == 2

    async def test_hydration_restores_the_window_oldest_first(self) -> None:
        """``get_records`` promises insertion order, oldest-first.

        The durable read is newest-first, so replaying it verbatim would put
        the window in the opposite order to every record appended after boot.
        """
        repo = _RecordingRepo()
        oldest = _record(0.25, at=_NOW - timedelta(hours=2))
        newest = _record(0.5, at=_NOW - timedelta(hours=1))
        repo.records.extend([newest, oldest])
        tracker = CostTracker(clock=FakeClock(start=_NOW))
        _attach(tracker, repo)

        await tracker.hydrate_from_durable()

        assert [r.cost for r in await tracker.get_records()] == [0.25, 0.5]

    async def test_hydration_does_not_double_count_a_record_already_held(
        self,
    ) -> None:
        """A hydration racing a live record must not bill the call twice.

        ``record()`` writes the durable row before it appends in memory, so a
        hydration overlapping it sees the same claim from both sides. Counting
        it twice inflates the window and can trip a hard stop that never
        happened.
        """
        repo = _RecordingRepo()
        tracker = CostTracker(clock=FakeClock(start=_NOW))
        _attach(tracker, repo)
        await tracker.record(_record(0.25))

        restored = await tracker.hydrate_from_durable()

        assert restored == 0
        assert await tracker.get_total_cost() == pytest.approx(0.25)

    async def test_hydration_leaves_the_newest_claims_protected(self) -> None:
        """The LRU exists to catch a redelivery, which is always recent.

        Replaying a newest-first read straight into the LRU makes the newest
        claim the least-recently-used one, so a capacity trim evicts exactly
        the claims a redelivery would repeat.
        """
        repo = _RecordingRepo()
        oldest = _record(1.0, at=_NOW - timedelta(hours=3))
        newest = _record(2.0, at=_NOW - timedelta(hours=1))
        repo.records.extend(
            [oldest, _record(4.0, at=_NOW - timedelta(hours=2)), newest]
        )
        tracker = CostTracker(claim_lru_capacity=2, clock=FakeClock(start=_NOW))
        _attach(tracker, repo)
        await tracker.hydrate_from_durable()

        # The newest claim is still in the LRU, so a redelivery is a no-op.
        await tracker.record(newest)

        assert await tracker.get_total_cost() == pytest.approx(7.0)
