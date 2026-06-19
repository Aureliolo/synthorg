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


class _FakeDurableStore:
    """Shared durable backing simulating the DB tables across a restart.

    The atomic ``increment_if_unseen`` writes both the dedup row and the
    aggregate in one transaction; a single store instance shared between
    the "pre-restart" and "post-restart" fake repos models how the rows
    survive the (simulated) process boundary, exactly as the
    SQLite/Postgres tables do.
    """

    def __init__(self) -> None:
        self.seen: set[str] = set()
        self.increment_calls: int = 0
        self.fail_atomic = False


class _FakeClaimSeenRepo:
    """Presence-only stand-in; the durable path no longer calls it directly.

    The tracker requires a claim-seen repo to be wired to take the atomic
    dedup path, but with the combined ``increment_if_unseen`` the dedup
    write lives on the aggregate repo, so this fake only needs to exist.
    """

    async def has_seen(self, *, claim_id: NotBlankStr) -> bool:
        return False

    async def mark_seen(
        self,
        *,
        claim_id: NotBlankStr,
        project_id: NotBlankStr,
        now: datetime,
        ttl_seconds: float,
    ) -> bool:
        return True

    async def prune_expired(self, now: datetime) -> int:
        return 0


class _FakeAggregateRepo:
    """Implements the atomic ``increment_if_unseen`` over a shared store."""

    def __init__(self, store: _FakeDurableStore) -> None:
        self._store = store

    @property
    def increment_calls(self) -> int:
        return self._store.increment_calls

    async def get(self, project_id: str) -> ProjectCostAggregate | None:
        return None

    def _aggregate(
        self, project_id: str, cost: float, in_tok: int, out_tok: int
    ) -> ProjectCostAggregate:
        return ProjectCostAggregate(
            project_id=project_id,
            total_cost=cost,
            currency=DEFAULT_CURRENCY,
            total_input_tokens=in_tok,
            total_output_tokens=out_tok,
            record_count=self._store.increment_calls,
            last_updated=datetime.now(UTC),
        )

    async def increment(
        self,
        project_id: str,
        cost: float,
        input_tokens: int,
        output_tokens: int,
        *,
        currency: str,
    ) -> ProjectCostAggregate:
        self._store.increment_calls += 1
        return self._aggregate(project_id, cost, input_tokens, output_tokens)

    async def increment_if_unseen(  # noqa: PLR0913 -- mirrors the real signature
        self,
        project_id: str,
        cost: float,
        input_tokens: int,
        output_tokens: int,
        *,
        currency: str,
        claim_id: NotBlankStr,
        now: datetime,
        ttl_seconds: float,
    ) -> tuple[ProjectCostAggregate | None, bool]:
        if self._store.fail_atomic:
            msg = "atomic write down"
            raise QueryError(msg)
        if str(claim_id) in self._store.seen:
            return None, False
        self._store.seen.add(str(claim_id))
        self._store.increment_calls += 1
        return self._aggregate(project_id, cost, input_tokens, output_tokens), True


async def test_restart_does_not_double_bill() -> None:
    """A redelivery after a restart re-bills only if the durable guard is absent."""
    store = _FakeDurableStore()
    record = make_cost_record(project_id="proj-1", cost=1.0)

    tracker1 = CostTracker(
        project_cost_repo=_FakeAggregateRepo(store),
        claim_seen_repo=_FakeClaimSeenRepo(),
    )
    await tracker1.record(record)
    assert store.increment_calls == 1

    # Restart: a fresh tracker has an empty in-memory LRU but shares the
    # durable store, so the redelivered record is recognised atomically.
    tracker2 = CostTracker(
        project_cost_repo=_FakeAggregateRepo(store),
        claim_seen_repo=_FakeClaimSeenRepo(),
    )
    await tracker2.record(record)
    assert store.increment_calls == 1


async def test_restart_double_bills_without_durable_guard() -> None:
    """Control: without the dedup repo, the restart hole re-bills (the bug)."""
    record = make_cost_record(project_id="proj-1", cost=1.0)

    # One durable store shared across the "restart": a fresh CostTracker
    # (empty in-memory LRU) processing the same record against the already
    # -incremented durable timeline is what actually exercises the
    # double-billing hole.
    store = _FakeDurableStore()
    await CostTracker(project_cost_repo=_FakeAggregateRepo(store)).record(record)
    assert store.increment_calls == 1

    await CostTracker(project_cost_repo=_FakeAggregateRepo(store)).record(record)
    # No durable guard across the restart -> the same claim is billed again.
    assert store.increment_calls == 2


async def test_atomic_failure_is_fail_open() -> None:
    """An atomic dedup+increment failure must not block the in-memory record."""
    store = _FakeDurableStore()
    store.fail_atomic = True
    tracker = CostTracker(
        project_cost_repo=_FakeAggregateRepo(store),
        claim_seen_repo=_FakeClaimSeenRepo(),
    )

    await tracker.record(make_cost_record(project_id="proj-1", cost=1.0))

    # Fail-open: the durable write failed but the in-memory record stands.
    assert store.increment_calls == 0
    assert await tracker.get_record_count() == 1


async def test_no_durable_dedup_without_project_id() -> None:
    """Records without a project_id never touch the durable store."""
    store = _FakeDurableStore()
    tracker = CostTracker(
        project_cost_repo=_FakeAggregateRepo(store),
        claim_seen_repo=_FakeClaimSeenRepo(),
    )

    await tracker.record(make_cost_record(project_id=None, cost=1.0))

    assert store.increment_calls == 0
    assert store.seen == set()
