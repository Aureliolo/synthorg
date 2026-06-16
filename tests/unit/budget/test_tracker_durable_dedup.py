"""Unit tests for CostTracker durable claim dedup (restart safety, audit 133).

The in-memory dedup LRU is empty after a process restart, so a JetStream
redelivery of an already-billed cost record would re-increment the durable
project aggregate. These tests prove the durable ``ProjectCostClaimSeenRepository``
guard survives a simulated restart (a fresh tracker over the same durable
store) and that the guard is fail-open on a transient DB error.
"""

from datetime import UTC, datetime

import pytest

from synthorg.budget.currency import DEFAULT_CURRENCY
from synthorg.budget.project_cost_aggregate import ProjectCostAggregate
from synthorg.budget.tracker import CostTracker
from synthorg.core.persistence_errors import QueryError
from synthorg.core.types import NotBlankStr

from .conftest import make_cost_record

pytestmark = pytest.mark.unit


class _FakeClaimSeenRepo:
    """In-memory dedup store simulating durability across a tracker restart.

    A single instance is shared between the "pre-restart" and
    "post-restart" trackers so the rows survive the (simulated) process
    boundary, exactly as the SQLite/Postgres tables do.
    """

    def __init__(self) -> None:
        self.rows: dict[str, str] = {}
        self.fail_has_seen = False
        self.fail_mark_seen = False

    async def has_seen(self, *, claim_id: NotBlankStr) -> bool:
        if self.fail_has_seen:
            msg = "lookup down"
            raise QueryError(msg)
        return str(claim_id) in self.rows

    async def mark_seen(
        self,
        *,
        claim_id: NotBlankStr,
        project_id: NotBlankStr,
        now: datetime,
        ttl_seconds: float,
    ) -> bool:
        if self.fail_mark_seen:
            msg = "write down"
            raise QueryError(msg)
        key = str(claim_id)
        if key in self.rows:
            return False
        self.rows[key] = str(project_id)
        return True

    async def prune_expired(self, now: datetime) -> int:
        return 0


def _make_increment_repo() -> _FakeAggregateRepo:
    return _FakeAggregateRepo()


class _FakeAggregateRepo:
    """Records each ``increment`` call so the test can count durable bills."""

    def __init__(self) -> None:
        self.increment_calls: int = 0

    async def get(self, project_id: str) -> ProjectCostAggregate | None:
        return None

    async def increment(
        self,
        project_id: str,
        cost: float,
        input_tokens: int,
        output_tokens: int,
        *,
        currency: str,
    ) -> ProjectCostAggregate:
        self.increment_calls += 1
        return ProjectCostAggregate(
            project_id=project_id,
            total_cost=cost,
            currency=DEFAULT_CURRENCY,
            total_input_tokens=input_tokens,
            total_output_tokens=output_tokens,
            record_count=self.increment_calls,
            last_updated=datetime.now(UTC),
        )


async def test_restart_does_not_double_bill() -> None:
    """A redelivery after a restart re-bills only if the durable guard is absent."""
    claim_seen = _FakeClaimSeenRepo()
    record = make_cost_record(project_id="proj-1", cost=1.0)

    repo1 = _make_increment_repo()
    tracker1 = CostTracker(project_cost_repo=repo1, claim_seen_repo=claim_seen)
    await tracker1.record(record)
    assert repo1.increment_calls == 1

    # Restart: a fresh tracker has an empty in-memory LRU but shares the
    # durable dedup store, so the redelivered record is recognised.
    repo2 = _make_increment_repo()
    tracker2 = CostTracker(project_cost_repo=repo2, claim_seen_repo=claim_seen)
    await tracker2.record(record)
    assert repo2.increment_calls == 0


async def test_restart_double_bills_without_durable_guard() -> None:
    """Control: without the durable repo, the restart hole re-bills (the bug)."""
    record = make_cost_record(project_id="proj-1", cost=1.0)

    repo1 = _make_increment_repo()
    await CostTracker(project_cost_repo=repo1).record(record)
    assert repo1.increment_calls == 1

    repo2 = _make_increment_repo()
    await CostTracker(project_cost_repo=repo2).record(record)
    # No durable guard, fresh LRU -> the duplicate re-increments.
    assert repo2.increment_calls == 1


async def test_mark_seen_failure_is_fail_open() -> None:
    """A durable mark_seen failure must not roll back the applied increment."""
    claim_seen = _FakeClaimSeenRepo()
    claim_seen.fail_mark_seen = True
    repo = _make_increment_repo()
    tracker = CostTracker(project_cost_repo=repo, claim_seen_repo=claim_seen)

    await tracker.record(make_cost_record(project_id="proj-1", cost=1.0))

    assert repo.increment_calls == 1
    assert await tracker.get_record_count() == 1


async def test_has_seen_failure_is_fail_open() -> None:
    """A durable lookup failure must not block a legitimate first record."""
    claim_seen = _FakeClaimSeenRepo()
    claim_seen.fail_has_seen = True
    repo = _make_increment_repo()
    tracker = CostTracker(project_cost_repo=repo, claim_seen_repo=claim_seen)

    await tracker.record(make_cost_record(project_id="proj-1", cost=1.0))

    assert repo.increment_calls == 1


async def test_no_durable_dedup_without_project_id() -> None:
    """Records without a project_id never touch the durable store."""
    claim_seen = _FakeClaimSeenRepo()
    repo = _make_increment_repo()
    tracker = CostTracker(project_cost_repo=repo, claim_seen_repo=claim_seen)

    await tracker.record(make_cost_record(project_id=None, cost=1.0))

    assert repo.increment_calls == 0
    assert claim_seen.rows == {}
