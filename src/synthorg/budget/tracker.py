# module-kind: complex_service
"""Real-time cost tracking service.

Provides an in-memory store with TTL-based eviction for
:class:`CostRecord` entries and aggregation queries consumed by the CFO
agent and budget monitoring.

Service layer for the cost tracking schema defined in the Operations
design page.  The current implementation is purely in-memory;
persistence integration is planned.
"""

import asyncio
import math
import time
from collections import OrderedDict, defaultdict
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Final, NamedTuple, override

from synthorg.budget._tracker_helpers import (
    _aggregate,
    _filter_records,
    _validate_time_range,
)
from synthorg.budget.currency import assert_currencies_match
from synthorg.budget.enums import BudgetAlertLevel
from synthorg.budget.errors import MixedCurrencyAggregationError
from synthorg.budget.spending_summary import (
    AgentSpending,
    DepartmentSpending,
)
from synthorg.constants import BUDGET_ROUNDING_PRECISION
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.budget import (
    BUDGET_AGENT_COST_QUERIED,
    BUDGET_DEPARTMENT_RESOLVE_FAILED,
    BUDGET_MIXED_CURRENCY_REJECTED,
    BUDGET_PENDING_RECORD_DRAIN_UNEXPECTED,
    BUDGET_PROJECT_COST_AGGREGATED,
    BUDGET_PROJECT_COST_AGGREGATION_FAILED,
    BUDGET_PROJECT_COST_QUERIED,
    BUDGET_PROJECT_RECORDS_QUERIED,
    BUDGET_PROVIDER_USAGE_QUERIED,
    BUDGET_RECORD_ADDED,
    BUDGET_RECORD_DEDUPED,
    BUDGET_RECORDS_AUTO_PRUNED,
    BUDGET_RECORDS_PRUNED,
    BUDGET_RECORDS_QUERIED,
    BUDGET_TOTAL_COST_QUERIED,
    BUDGET_TRACKER_CLEARED,
    BUDGET_TRACKER_CREATED,
)
from synthorg.observability.metrics_hub import record_budget_query

if TYPE_CHECKING:
    from collections.abc import Callable

    from synthorg.budget.config import BudgetConfig
    from synthorg.budget.cost_record import CostRecord
    from synthorg.persistence.project_cost_aggregate_protocol import (
        ProjectCostAggregateRepository,
    )

from synthorg.core.types import NotBlankStr

logger = get_logger(__name__)

_COST_WINDOW_HOURS: Final[int] = 168  # 7 days
_AUTO_PRUNE_THRESHOLD: Final[int] = 100_000

#: Default capacity of the per-tracker LRU set used to dedupe
#: ``CostRecord.claim_id``.  Sized as 10% of ``_AUTO_PRUNE_THRESHOLD``
#: (the 7-day per-tracker total-event cap, 100k events): the dedup
#: set only needs to cover the redelivery / retry window, not the
#: full 7-day archive.  10k entries comfortably outlasts JetStream's
#: default redelivery horizon and any reasonable in-process retry
#: while keeping the LRU footprint bounded so a misbehaving caller
#: spamming unique ``claim_id`` values cannot grow it without limit.
_DEFAULT_CLAIM_LRU_CAPACITY: Final[int] = 10_000


class ProviderUsageSummary(NamedTuple):
    """Per-provider usage totals for a time window."""

    total_tokens: int
    total_cost: float


# Imported after ProviderUsageSummary: tracker_summary imports that symbol from
# this module, so the mixin must be loaded after its dependencies are defined.
from synthorg.budget.tracker_summary import CostTrackerSummaryMixin  # noqa: E402


class CostTracker(CostTrackerSummaryMixin):
    """In-memory cost tracking service with TTL-based eviction.

    Records :class:`CostRecord` entries from LLM API calls and provides
    aggregation queries for budget monitoring.  Memory is bounded by a
    soft TTL-based auto-prune: when the record count exceeds
    *auto_prune_threshold*, records older than 168 hours (7 days)
    are removed on the next query.

    Args:
        budget_config: Optional budget configuration for alert level
            computation.  When ``None``, alert level defaults to
            ``NORMAL`` and ``budget_used_percent`` to ``0.0``.
        department_resolver: Optional callable mapping ``agent_id`` to a
            department name.  When ``None`` or returning ``None`` for an
            agent, the agent is excluded from department aggregation.
        auto_prune_threshold: Maximum record count before auto-pruning
            is triggered on snapshot.  Defaults to 100,000.

    Raises:
        ValueError: If *auto_prune_threshold* < 1.
    """

    def __init__(  # noqa: PLR0913 -- composable construction surface; all kwargs optional
        self,
        *,
        budget_config: BudgetConfig | None = None,
        department_resolver: Callable[[str], str | None] | None = None,
        auto_prune_threshold: int = _AUTO_PRUNE_THRESHOLD,
        project_cost_repo: ProjectCostAggregateRepository | None = None,
        claim_lru_capacity: int = _DEFAULT_CLAIM_LRU_CAPACITY,
        clock: Clock | None = None,
    ) -> None:
        if auto_prune_threshold < 1:
            msg = f"auto_prune_threshold must be >= 1, got {auto_prune_threshold}"
            raise ValueError(msg)
        if claim_lru_capacity < 1:
            msg = f"claim_lru_capacity must be >= 1, got {claim_lru_capacity}"
            raise ValueError(msg)
        self._records: list[CostRecord] = []
        # Defer Lock construction until the first async method runs so
        # the lock binds to the live event loop, not whichever loop (if
        # any) was current when the tracker was constructed. xdist
        # workers tear down their loop between tests and a Lock bound
        # to a closed loop deadlocks the next test.
        self._lock: asyncio.Lock | None = None
        self._budget_config = budget_config
        self._department_resolver = department_resolver
        self._auto_prune_threshold = auto_prune_threshold
        self._project_cost_repo = project_cost_repo
        self._clock: Clock = clock or SystemClock()
        # Strong references to in-flight background recording tasks
        # scheduled by the cost-recording chokepoint. Owned by the
        # tracker (one per :class:`AppState`, fresh per test) so xdist
        # workers cannot leak tasks bound to a closed event loop into
        # the next test's loop. Tasks self-evict on completion via
        # ``add_done_callback(self._pending_record_tasks.discard)``.
        self._pending_record_tasks: set[asyncio.Task[None]] = set()
        # Bounded LRU of finalised claim_ids the tracker has already
        # appended. Stored as ``OrderedDict[str, None]`` so re-
        # submission moves the key to the tail in O(1) and the head
        # can be popped on capacity overflow without scanning.
        # In-flight reservations are kept in a separate set so the
        # capacity trim never evicts a claim that is still being
        # processed; mixing both states in one ``OrderedDict`` would
        # let the trim pop a still-running reservation and allow a
        # duplicate to slip past the membership check.
        self._inflight_claims: set[str] = set()
        self._seen_claims: OrderedDict[str, None] = OrderedDict()
        self._claim_lru_capacity = claim_lru_capacity
        logger.debug(
            BUDGET_TRACKER_CREATED,
            has_budget_config=budget_config is not None,
            has_department_resolver=department_resolver is not None,
            has_project_cost_repo=project_cost_repo is not None,
            claim_lru_capacity=claim_lru_capacity,
        )

    def _get_lock(self) -> asyncio.Lock:
        """Return the per-loop lock, creating it on first use.

        asyncio is single-threaded per loop, so the ``is None`` check
        and assignment cannot race within a loop. Constructing the
        lock lazily lets the tracker survive xdist workers that
        recreate the event loop between tests.

        Returns:
            Result of type ``asyncio.Lock``.
        """
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    @property
    def budget_config(self) -> BudgetConfig | None:
        """The optional budget configuration.

        Returns:
            Budget config if set, else ``None``.
        """
        return self._budget_config

    async def record(self, cost_record: CostRecord) -> None:
        """Append a cost record.

        The in-memory append runs under ``_lock``.  After the lock
        is released, ``_update_project_aggregate`` is awaited to
        update the durable project cost aggregate when the record
        has a ``project_id`` and a repository is configured.
        Aggregate updates are best-effort: failures are logged at
        WARNING but do not affect the in-memory recording.

        When a ``BudgetConfig`` is attached, the incoming record's
        ``currency`` must match ``budget_config.currency``; mismatches
        raise :class:`MixedCurrencyAggregationError` at the ingestion
        boundary so downstream aggregators never see mixed-currency
        data in the first place.

        Idempotency: a bounded LRU of accepted ``cost_record.claim_id``
        values protects against double-bills from JetStream redelivery
        or in-process retries. Repeat submissions are no-ops and emit
        ``BUDGET_RECORD_DEDUPED`` at INFO. Eviction at the LRU
        capacity is best-effort; once a claim ages out, a re-submitted
        record with the same key is treated as fresh.

        Args:
            cost_record: Immutable cost record to store.

        Raises:
            MixedCurrencyAggregationError: If the record's currency
                does not match the configured ``budget.currency``.
            BaseException: Raised when the relevant invariant fails.
        """
        # Currency check first -- it's synchronous, has no in-flight
        # state to roll back, and a mismatch is a hard caller-contract
        # violation that must surface BEFORE we reserve a claim_id
        # slot. Per-project same-currency invariant is enforced by
        # ``ProjectCostAggregateRepository.increment``; no tracker-
        # side pin is required there.
        if (
            self._budget_config is not None
            and cost_record.currency != self._budget_config.currency
        ):
            # Log at WARNING with full record + budget context BEFORE
            # raising so a downstream catch-and-translate cannot hide
            # repeated mismatches from operators.
            logger.warning(
                BUDGET_MIXED_CURRENCY_REJECTED,
                agent_id=cost_record.agent_id,
                task_id=cost_record.task_id,
                project_id=cost_record.project_id,
                record_currency=cost_record.currency,
                budget_currency=self._budget_config.currency,
            )
            msg = (
                f"Record currency {cost_record.currency!r} does not match "
                f"configured budget currency "
                f"{self._budget_config.currency!r}"
            )
            raise MixedCurrencyAggregationError(
                msg,
                currencies=frozenset(
                    {
                        cost_record.currency,
                        self._budget_config.currency,
                    }
                ),
                agent_id=cost_record.agent_id,
                task_id=cost_record.task_id,
                project_id=cost_record.project_id,
            )

        # Idempotency fast-path with in-flight reservation. ``_lock``
        # protects both ``_inflight_claims`` (set of in-flight reservations)
        # and ``_seen_claims`` (bounded LRU of finalised entries). We
        # check both states under the lock, then either dedupe (if
        # already seen / in flight) or reserve the claim in
        # ``_inflight_claims`` so a concurrent second call sees the
        # entry and dedupes immediately. Keeping in-flight separate
        # from the LRU prevents the capacity trim from popping a
        # still-running reservation, which would let a duplicate
        # slip past the membership check.
        async with self._get_lock():
            if (
                cost_record.claim_id in self._inflight_claims
                or cost_record.claim_id in self._seen_claims
            ):
                if cost_record.claim_id in self._seen_claims:
                    self._seen_claims.move_to_end(cost_record.claim_id)
                logger.info(
                    BUDGET_RECORD_DEDUPED,
                    claim_id=cost_record.claim_id,
                    agent_id=cost_record.agent_id,
                    task_id=cost_record.task_id,
                    provider=cost_record.provider,
                    model=cost_record.model,
                    cost=cost_record.cost,
                )
                return
            self._inflight_claims.add(cost_record.claim_id)

        # Run the durable aggregate update OUTSIDE the lock -- DB I/O
        # must not block concurrent in-memory readers/writers. Any
        # failure releases the in-flight reservation so a retry with
        # the same claim_id is not falsely deduped.
        try:
            await self._update_project_aggregate(cost_record)
        except BaseException:
            async with self._get_lock():
                self._inflight_claims.discard(cost_record.claim_id)
            raise

        async with self._get_lock():
            # Promote the reservation to a finalised LRU entry under
            # the lock so the membership check above never observes
            # a gap where the claim is in neither set. Eviction only
            # affects ``_seen_claims``, so still-running reservations
            # in ``_inflight_claims`` are untouched.
            self._inflight_claims.discard(cost_record.claim_id)
            self._records.append(cost_record)
            self._seen_claims[cost_record.claim_id] = None
            self._seen_claims.move_to_end(cost_record.claim_id)
            while len(self._seen_claims) > self._claim_lru_capacity:
                self._seen_claims.popitem(last=False)
            logger.info(
                BUDGET_RECORD_ADDED,
                agent_id=cost_record.agent_id,
                model=cost_record.model,
                cost=cost_record.cost,
            )

    async def prune_expired(self, *, now: datetime | None = None) -> int:
        """Remove records older than the 168-hour (7-day) cost window.

        Call periodically from long-running services to bound
        memory growth.

        Args:
            now: Reference time.  Defaults to ``self._clock.now()`` so
                ``FakeClock`` injection deterministically controls the
                eviction cutoff.

        Returns:
            Number of records removed.
        """
        ref = now or self._clock.now()
        cutoff = ref - timedelta(hours=_COST_WINDOW_HOURS)
        async with self._get_lock():
            pruned = self._prune_before(cutoff)
            if pruned:
                logger.info(
                    BUDGET_RECORDS_PRUNED,
                    pruned=pruned,
                    remaining=len(self._records),
                )
            return pruned

    async def get_total_cost(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> float:
        """Sum of ``cost`` across all records, optionally filtered by time.

        Args:
            start: Inclusive lower bound on ``timestamp``.
            end: Exclusive upper bound on ``timestamp``.

        Returns:
            Rounded total cost in the configured currency.

        Raises:
            ValueError: If both *start* and *end* are given and
                ``start >= end``.
        """
        query_start = time.perf_counter()
        try:
            _validate_time_range(start, end)
            logger.debug(BUDGET_TOTAL_COST_QUERIED, start=start, end=end)
            snapshot = await self._snapshot()
            filtered = _filter_records(snapshot, start=start, end=end)
            return _aggregate(filtered).cost
        finally:
            record_budget_query(
                query_type="total_cost",
                duration_sec=time.perf_counter() - query_start,
            )

    async def get_agent_cost(
        self,
        agent_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> float:
        """Sum of ``cost`` for a single agent, optionally filtered by time.

        Args:
            agent_id: Agent identifier to filter by.
            start: Inclusive lower bound on ``timestamp``.
            end: Exclusive upper bound on ``timestamp``.

        Returns:
            Rounded total cost in the configured currency for the agent.

        Raises:
            ValueError: If both *start* and *end* are given and
                ``start >= end``.
        """
        query_start = time.perf_counter()
        try:
            _validate_time_range(start, end)
            logger.debug(
                BUDGET_AGENT_COST_QUERIED,
                agent_id=agent_id,
                start=start,
                end=end,
            )
            snapshot = await self._snapshot()
            filtered = _filter_records(
                snapshot,
                agent_id=agent_id,
                start=start,
                end=end,
            )
            return _aggregate(filtered).cost
        finally:
            record_budget_query(
                query_type="agent_cost",
                duration_sec=time.perf_counter() - query_start,
            )

    async def get_project_cost(
        self,
        project_id: NotBlankStr,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> float:
        """Sum of ``cost`` for a single project.

        Args:
            project_id: Project identifier to filter by.
            start: Inclusive lower bound on ``timestamp``.
            end: Exclusive upper bound on ``timestamp``.

        Returns:
            Rounded total cost in the configured currency for the project.

        Raises:
            ValueError: If both *start* and *end* are given and
                ``start >= end``.
        """
        query_start = time.perf_counter()
        try:
            _validate_time_range(start, end)
            logger.debug(
                BUDGET_PROJECT_COST_QUERIED,
                project_id=project_id,
                start=start,
                end=end,
            )
            snapshot = await self._snapshot()
            filtered = _filter_records(
                snapshot,
                project_id=project_id,
                start=start,
                end=end,
            )
            return _aggregate(filtered).cost
        finally:
            record_budget_query(
                query_type="project_cost",
                duration_sec=time.perf_counter() - query_start,
            )

    async def get_project_records(
        self,
        project_id: NotBlankStr,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[CostRecord, ...]:
        """Return cost records for a specific project.

        Args:
            project_id: Project identifier to filter by.
            start: Inclusive lower bound on ``timestamp``.
            end: Exclusive upper bound on ``timestamp``.

        Returns:
            Immutable tuple of matching cost records.

        Raises:
            ValueError: If both *start* and *end* are given and
                ``start >= end``.
        """
        _validate_time_range(start, end)
        logger.debug(
            BUDGET_PROJECT_RECORDS_QUERIED,
            project_id=project_id,
            start=start,
            end=end,
        )
        snapshot = await self._snapshot()
        return _filter_records(
            snapshot,
            project_id=project_id,
            start=start,
            end=end,
        )

    async def get_record_count(self) -> int:
        """Total number of recorded cost entries.

        Returns:
            Number of cost records.
        """
        async with self._get_lock():
            return len(self._records)

    async def get_records(
        self,
        *,
        agent_id: str | None = None,
        task_id: str | None = None,
        provider: NotBlankStr | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[CostRecord, ...]:
        """Return filtered cost records.

        Returns an immutable snapshot of records matching the filters.

        Args:
            agent_id: Filter by agent.
            task_id: Filter by task.
            provider: Filter by provider name.
            start: Inclusive lower bound on ``timestamp``.
            end: Exclusive upper bound on ``timestamp``.

        Returns:
            Immutable tuple of matching cost records.

        Raises:
            ValueError: If both *start* and *end* are given and
                ``start >= end``.
        """
        _validate_time_range(start, end)
        logger.debug(
            BUDGET_RECORDS_QUERIED,
            agent_id=agent_id,
            task_id=task_id,
            provider=provider,
            start=start,
            end=end,
        )
        snapshot = await self._snapshot()
        return _filter_records(
            snapshot,
            agent_id=agent_id,
            task_id=task_id,
            provider=provider,
            start=start,
            end=end,
        )

    async def get_provider_usage(
        self,
        provider_name: NotBlankStr,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> ProviderUsageSummary:
        """Return aggregated token and cost totals for a provider.

        Args:
            provider_name: Provider to aggregate usage for.
            start: Inclusive lower bound on ``timestamp``.
            end: Exclusive upper bound on ``timestamp``.

        Returns:
            Total tokens (input + output) and total cost.

        Raises:
            ValueError: If both *start* and *end* are given and
                ``start >= end``.
        """
        _validate_time_range(start, end)
        logger.debug(
            BUDGET_PROVIDER_USAGE_QUERIED,
            provider=provider_name,
            start=start,
            end=end,
        )
        snapshot = await self._snapshot()
        filtered = _filter_records(
            snapshot,
            provider=provider_name,
            start=start,
            end=end,
        )
        if not filtered:
            return ProviderUsageSummary(total_tokens=0, total_cost=0.0)
        agg = _aggregate(filtered)
        return ProviderUsageSummary(
            total_tokens=agg.input_tokens + agg.output_tokens,
            total_cost=agg.cost,
        )

    def clear(self) -> None:
        """Reset all recorded cost data for test isolation.

        Deliberately synchronous and does **not** acquire ``_lock``.
        ``record()`` and ``_snapshot()`` serialise concurrent async
        readers/writers via ``async with self._lock``; ``clear()`` is
        the test-only counterpart, called from sync test-fixture
        setup *after* the previous event loop has closed and cancelled
        any in-flight tasks.  Under that invariant no async coroutine
        can race with this method, and ``list.clear()`` is a single
        atomic C-level operation under the GIL.  Calling this method
        from production / async code is unsupported.

        Also resets ``_seen_claims`` and ``_inflight_claims`` so a
        reused ``claim_id`` on a fresh test does not get falsely
        deduped.
        """
        cleared_count = len(self._records)
        self._records.clear()
        self._seen_claims.clear()
        self._inflight_claims.clear()
        logger.info(BUDGET_TRACKER_CLEARED, cleared_count=cleared_count)

    def track_pending_record(self, task: asyncio.Task[None]) -> None:
        """Hold a strong reference to a background recording task.

        The cost-recording chokepoint schedules ``cost_tracker.record(...)``
        as a background task so the user-visible ``provider.complete()``
        response is never blocked on tracker I/O. asyncio's loop only
        keeps weak references to tasks, so without an external strong
        reference the loop's GC may cancel an in-flight task. This
        method registers the strong reference and wires a self-eviction
        callback so the set never grows beyond in-flight tasks.

        Tracker ownership of the set means each test (which constructs
        its own :class:`CostTracker`) gets a fresh, isolated set --
        leaked tasks from a prior test bound to a closed event loop
        cannot poison the next test's loop.
        """
        self._pending_record_tasks.add(task)
        task.add_done_callback(self._pending_record_tasks.discard)

    async def drain_pending_records(self) -> None:
        """Wait for all in-flight background record tasks to settle.

        Test-only utility: tests that need to observe ``CostTracker``
        state immediately after a ``provider.complete()`` call can
        ``await tracker.drain_pending_records()`` to deterministically
        wait for the recording side effect.

        No-op when there are no pending tasks. Recoverable failures
        inside the background tasks are already logged + swallowed in
        ``_record_cost_in_background`` (see
        :mod:`synthorg.providers.cost_recording`).
        :class:`MemoryError` and :class:`RecursionError` propagate so a
        ``drain`` invoked from a test path doesn't silently swallow
        interpreter-fatal signals via ``return_exceptions=True``.
        :class:`asyncio.CancelledError` is re-raised so cancellation
        propagates instead of producing a misleading WARN log: a
        cancelled background task is the *expected* outcome of a
        graceful shutdown or a test cancelling the surrounding
        ``TaskGroup``, not a regression.

        Raises:
            MemoryError: Propagated from a background task, never swallowed.
            RecursionError: Propagated from a background task, never swallowed.
            CancelledError: Re-raised so cancellation propagates.
        """
        if not self._pending_record_tasks:
            return
        # Snapshot before awaiting: ``_pending_record_tasks`` is mutated
        # by the ``add_done_callback`` registered above, and iterating
        # the live set while it shrinks would risk skipping tasks.
        pending = tuple(self._pending_record_tasks)
        results = await asyncio.gather(*pending, return_exceptions=True)
        cancelled_count = 0
        for outcome in results:
            if isinstance(outcome, (MemoryError, RecursionError)):
                raise outcome
            if isinstance(outcome, asyncio.CancelledError):
                # Cancellation is expected during graceful shutdown;
                # count for the propagation below but don't WARN.
                cancelled_count += 1
                continue
            if isinstance(outcome, BaseException):
                # ``_record_cost_in_background`` already logs + swallows
                # recoverable failures, so reaching this branch means
                # something downstream raised without going through the
                # documented logging path. Surface defensively at WARN
                # so the regression is visible in test output rather
                # than silently dropped by ``return_exceptions=True``.
                logger.warning(
                    BUDGET_PENDING_RECORD_DRAIN_UNEXPECTED,
                    error_type=type(outcome).__name__,
                    error=safe_error_description(outcome),
                )
        if cancelled_count:
            # Re-raise a CancelledError so the caller's surrounding
            # TaskGroup / context observes the cancellation instead of
            # silently masking it. Specific instance is not preserved
            # because the gather snapshot may hold many; one suffices
            # to propagate the signal.
            raise asyncio.CancelledError

    # ── Private helpers ──────────────────────────────────────────────

    async def _update_project_aggregate(
        self,
        cost_record: CostRecord,
    ) -> None:
        """Best-effort update of the durable project cost aggregate.

        No-op when the record has no ``project_id`` or no repository
        is configured.  Failures (other than
        :class:`MixedCurrencyAggregationError`, which propagates as a
        data-integrity error the caller must see) are logged at
        WARNING and swallowed.

        Raises:
            MixedCurrencyAggregationError: If the related operation fails.
        """
        if self._project_cost_repo is None or cost_record.project_id is None:
            return

        try:
            await self._project_cost_repo.increment(
                cost_record.project_id,
                cost_record.cost,
                cost_record.input_tokens,
                cost_record.output_tokens,
                currency=cost_record.currency,
            )
            logger.debug(
                BUDGET_PROJECT_COST_AGGREGATED,
                project_id=cost_record.project_id,
                cost=cost_record.cost,
                currency=cost_record.currency,
            )
        except MixedCurrencyAggregationError as exc:
            # Mixed-currency increments are a caller-contract violation;
            # surface to the caller rather than silently swallowing --
            # but log first so operators see the rejection in telemetry
            # alongside successful aggregations.
            logger.warning(
                BUDGET_PROJECT_COST_AGGREGATION_FAILED,
                project_id=cost_record.project_id,
                cost=cost_record.cost,
                currency=cost_record.currency,
                error_type=type(exc).__qualname__,
                reason="mixed_currency_aggregation",
            )
            raise
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                BUDGET_PROJECT_COST_AGGREGATION_FAILED,
                project_id=cost_record.project_id,
                cost=cost_record.cost,
            )

    @override
    async def _snapshot(
        self,
        *,
        now: datetime | None = None,
    ) -> tuple[CostRecord, ...]:
        """Return an immutable snapshot of all current records.

        When the record count exceeds the auto-prune threshold,
        expired records are removed before the snapshot is taken.

        Args:
            now: Reference time for auto-prune cutoff.  Defaults to
                ``self._clock.now()`` so ``FakeClock`` injection
                deterministically controls auto-prune timing.

        Returns:
            Tuple of ``CostRecord``.
        """
        async with self._get_lock():
            if len(self._records) > self._auto_prune_threshold:
                ref = now or self._clock.now()
                cutoff = ref - timedelta(hours=_COST_WINDOW_HOURS)
                pruned = self._prune_before(cutoff)
                if pruned:
                    logger.info(
                        BUDGET_RECORDS_AUTO_PRUNED,
                        pruned=pruned,
                        remaining=len(self._records),
                    )
            return tuple(self._records)

    def _prune_before(self, cutoff: datetime) -> int:
        """Remove records older than *cutoff*.  Caller must hold ``_lock``.

        Returns:
            Result of type ``int``.
        """
        if not self._records:
            return 0
        before = len(self._records)
        self._records = [r for r in self._records if r.timestamp >= cutoff]
        return before - len(self._records)

    @override
    def _build_dept_spendings(
        self,
        agent_spendings: list[AgentSpending],
    ) -> list[DepartmentSpending]:
        """Aggregate per-department spending from agent spendings.

        Returns:
            List of ``DepartmentSpending``.

        Raises:
            MixedCurrencyAggregationError: If two agents assigned to
                the same department have different currencies on their
                ``AgentSpending`` rollups.
        """
        dept_map: dict[str, list[AgentSpending]] = defaultdict(list)
        for agent_spend in agent_spendings:
            dept = self._resolve_department(agent_spend.agent_id)
            if dept is not None:
                dept_map[dept].append(agent_spend)

        results: list[DepartmentSpending] = []
        for dname, spends in sorted(dept_map.items()):
            dept_currency = assert_currencies_match(
                (s.currency for s in spends),
                department_id=dname,
            )
            results.append(
                DepartmentSpending(
                    department_name=dname,
                    total_cost=round(
                        math.fsum(s.total_cost for s in spends),
                        BUDGET_ROUNDING_PRECISION,
                    ),
                    currency=dept_currency,
                    total_input_tokens=sum(s.total_input_tokens for s in spends),
                    total_output_tokens=sum(s.total_output_tokens for s in spends),
                    record_count=sum(s.record_count for s in spends),
                )
            )
        return results

    @override
    def _build_budget_context(
        self,
        total_cost: float,
    ) -> tuple[float, float, BudgetAlertLevel]:
        """Compute budget monthly, used percentage, and alert level.

        Returns:
            Tuple ``(float, float, BudgetAlertLevel)``.
        """
        budget_monthly = (
            self._budget_config.total_monthly if self._budget_config else 0.0
        )
        used_pct = (
            round(
                total_cost / budget_monthly * 100,
                BUDGET_ROUNDING_PRECISION,
            )
            if budget_monthly > 0
            else 0.0
        )
        alert = self._compute_alert_level(used_pct)
        return budget_monthly, used_pct, alert

    @override
    def _compute_alert_level(self, used_pct: float) -> BudgetAlertLevel:
        """Determine alert level from the rounded budget percentage.

        Returns:
            Result of type ``BudgetAlertLevel``.
        """
        if self._budget_config is None or self._budget_config.total_monthly <= 0:
            return BudgetAlertLevel.NORMAL

        alerts = self._budget_config.alerts

        if used_pct >= alerts.hard_stop_at:
            return BudgetAlertLevel.HARD_STOP
        if used_pct >= alerts.critical_at:
            return BudgetAlertLevel.CRITICAL
        if used_pct >= alerts.warn_at:
            return BudgetAlertLevel.WARNING
        return BudgetAlertLevel.NORMAL

    @override
    def _resolve_department(self, agent_id: str) -> str | None:
        """Resolve agent to department, logging resolver errors.

        Returns:
            The matching ``str``, or ``None`` when no match is found.
        """
        if self._department_resolver is None:
            return None
        try:
            return self._department_resolver(agent_id)
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                BUDGET_DEPARTMENT_RESOLVE_FAILED,
                agent_id=agent_id,
                error=safe_error_description(exc),
                error_type=type(exc).__qualname__,
            )
            return None
