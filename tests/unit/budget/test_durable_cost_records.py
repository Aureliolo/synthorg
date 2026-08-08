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
from synthorg.core.persistence_errors import QueryError
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
    """In-memory stand-in for the durable cost-record store."""

    def __init__(self) -> None:
        self.records: list[CostRecord] = []

    async def append(self, record: CostRecord) -> None:
        self.records.append(record)

    async def query(
        self,
        filter_spec: CostRecordFilterSpec,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[CostRecord, ...]:
        rows = self.records
        if filter_spec.since is not None:
            rows = [r for r in rows if r.timestamp >= filter_spec.since]
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

        with structlog.testing.capture_logs():
            await tracker.record(_record(0.25))

        assert await tracker.get_total_cost() == pytest.approx(0.25)


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
