# module-kind: complex_service
"""Real-time cost tracking service.

Provides an in-memory store with TTL-based eviction for
:class:`CostRecord` entries and aggregation queries consumed by the CFO
agent and budget monitoring.

Service layer for the cost tracking schema defined in the Operations
design page. The in-memory window is a cache over the durable
``cost_records`` table: every accepted record is appended there, and the
window is rehydrated from it on boot so a ceiling survives a restart.
"""

import asyncio
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta
from typing import Final, NamedTuple, override

from synthorg.budget._tracker_helpers import (
    _aggregate,
    _filter_records,
    _validate_time_range,
)
from synthorg.budget.billing_model_port import BillingModelResolver
from synthorg.budget.config import BudgetConfig
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.errors import MixedCurrencyAggregationError
from synthorg.budget.spending_summary import (
    QualifiedTotal,
    measurability_of,
)
from synthorg.budget.tracker_summary import CostTrackerSummaryMixin
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.pagination import (
    DEFAULT_LIST_LIMIT,
    MAX_LIST_LIMIT,
    paginate,
    validate_pagination_args,
)
from synthorg.core.persistence_errors import PersistenceError
from synthorg.core.resilience import GeneralRetryHandler
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.budget import (
    BUDGET_AGENT_COST_QUERIED,
    BUDGET_HYDRATED,
    BUDGET_HYDRATION_FAILED,
    BUDGET_MIXED_CURRENCY_REJECTED,
    BUDGET_PENDING_RECORD_DRAIN_UNEXPECTED,
    BUDGET_PROJECT_COST_AGGREGATED,
    BUDGET_PROJECT_COST_AGGREGATION_FAILED,
    BUDGET_PROJECT_COST_QUERIED,
    BUDGET_PROJECT_RECORDS_QUERIED,
    BUDGET_PROVIDER_USAGE_QUERIED,
    BUDGET_RECORD_ADDED,
    BUDGET_RECORD_DEDUPED,
    BUDGET_RECORD_PERSIST_FAILED,
    BUDGET_RECORD_PERSIST_RECOVERED,
    BUDGET_RECORD_PERSIST_RETRIED,
    BUDGET_RECORDS_AUTO_PRUNED,
    BUDGET_RECORDS_PRUNED,
    BUDGET_RECORDS_QUERIED,
    BUDGET_TOTAL_COST_QUERIED,
    BUDGET_TRACKER_CLEARED,
    BUDGET_TRACKER_CREATED,
)
from synthorg.observability.metrics_hub import record_budget_query
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

logger = get_logger(__name__)

_COST_WINDOW_HOURS: Final[int] = 168  # 7 days
_AUTO_PRUNE_THRESHOLD: Final[int] = 100_000

#: Attempts (including the first) for one durable cost-record append. The
#: record is a single-row insert on the tail of a provider call that has
#: already returned, so the retry is cheap; a storage problem outlasting
#: three quick tries will not be fixed by a fourth.
_DURABLE_APPEND_MAX_ATTEMPTS: Final[int] = 3

#: Backoff bounds for that retry. Sub-second on purpose: the append runs on a
#: background task, but it holds the record the ceiling is enforced from and
#: must not become a queue.
_DURABLE_APPEND_BASE_DELAY_SECONDS: Final[float] = 0.05
_DURABLE_APPEND_DELAY_CAP_SECONDS: Final[float] = 0.4

#: Consecutive dropped records after which the log stops calling it a blip.
#: One lost receipt under-reports a call; a run of them means spend is not
#: being recorded, which is the whole failure this path closes.
_PERSIST_FAILURE_ESCALATION_STREAK: Final[int] = 3


def _durable_write_is_retryable(exc: Exception) -> bool:
    """Whether *exc* is worth another append attempt.

    Args:
        exc: The exception the append raised.

    Returns:
        ``True`` for a persistence error the layer marks transient. A
        constraint violation (the claim key already landed) reproduces on
        every attempt and is not retried.
    """
    return isinstance(exc, PersistenceError) and exc.is_retryable


#: Default capacity of the per-tracker LRU set used to dedupe
#: ``CostRecord.claim_id``.  Sized as 10% of ``_AUTO_PRUNE_THRESHOLD``
#: (the 7-day per-tracker total-event cap, 100k events): the dedup
#: set only needs to cover the redelivery / retry window, not the
#: full 7-day archive.  10k entries comfortably outlasts JetStream's
#: default redelivery horizon and any reasonable in-process retry
#: while keeping the LRU footprint bounded so a misbehaving caller
#: spamming unique ``claim_id`` values cannot grow it without limit.
_DEFAULT_CLAIM_LRU_CAPACITY: Final[int] = 10_000

#: Default TTL for a durable cost-claim dedup row. The row only needs to
#: outlive the maximum redelivery / restart-replay horizon; 7 days
#: comfortably covers a JetStream redelivery window plus an extended
#: container outage while letting ``prune_expired`` reclaim stale rows.
_DEFAULT_CLAIM_SEEN_TTL_SECONDS: Final[float] = 604800.0


class ProviderUsageSummary(NamedTuple):
    """Per-provider usage totals for a time window."""

    total_tokens: int
    total_cost: float


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

    def __init__(
        self,
        *,
        budget_config: BudgetConfig | None = None,
        department_resolver: Callable[[str], str | None] | None = None,
        auto_prune_threshold: int = _AUTO_PRUNE_THRESHOLD,
        project_cost_repo: ProjectCostAggregateRepository | None = None,
        claim_seen_repo: ProjectCostClaimSeenRepository | None = None,
        claim_seen_ttl_seconds: float = _DEFAULT_CLAIM_SEEN_TTL_SECONDS,
        claim_lru_capacity: int = _DEFAULT_CLAIM_LRU_CAPACITY,
        clock: Clock | None = None,
    ) -> None:
        if auto_prune_threshold < 1:
            msg = f"auto_prune_threshold must be >= 1, got {auto_prune_threshold}"
            raise ValueError(msg)
        if claim_lru_capacity < 1:
            msg = f"claim_lru_capacity must be >= 1, got {claim_lru_capacity}"
            raise ValueError(msg)
        if claim_seen_ttl_seconds <= 0:
            msg = f"claim_seen_ttl_seconds must be > 0, got {claim_seen_ttl_seconds}"
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
        self._claim_seen_repo = claim_seen_repo
        self._cost_record_repo: CostRecordRepository | None = None
        self._billing_model_resolver: BillingModelResolver | None = None
        self._claim_seen_ttl_seconds = claim_seen_ttl_seconds
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
        self._consecutive_persist_failures = 0
        self._durable_retry = GeneralRetryHandler(
            retryable=_durable_write_is_retryable,
            max_attempts=_DURABLE_APPEND_MAX_ATTEMPTS,
            base=_DURABLE_APPEND_BASE_DELAY_SECONDS,
            cap=_DURABLE_APPEND_DELAY_CAP_SECONDS,
            event=BUDGET_RECORD_PERSIST_RETRIED,
            clock=self._clock,
        )
        logger.debug(
            BUDGET_TRACKER_CREATED,
            has_budget_config=budget_config is not None,
            has_department_resolver=department_resolver is not None,
            has_project_cost_repo=project_cost_repo is not None,
            has_claim_seen_repo=claim_seen_repo is not None,
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

    def set_budget_config(self, budget_config: BudgetConfig) -> None:
        """Adopt a re-resolved budget config for subsequent records.

        The tracker is built once per process, and what it holds is not
        inert: ``currency`` decides which records it accepts at all, so a
        currency change left unapplied here makes the tracker reject every
        record written in the currency the operator just chose. The monthly
        total and reset day it exposes are what the summaries and the
        Prometheus budget gauges are computed from.

        Args:
            budget_config: The freshly resolved configuration.
        """
        self._budget_config = budget_config

    def attach_durable_repos(
        self,
        *,
        project_cost_repo: ProjectCostAggregateRepository,
        claim_seen_repo: ProjectCostClaimSeenRepository,
        cost_record_repo: CostRecordRepository | None = None,
    ) -> None:
        """Attach the durable project-cost write + dedup repos post-connect.

        The tracker is constructed at the synchronous construction phase
        before persistence is connected, so the durable repos are wired
        here once a backend is available. Called once at boot before the
        app serves traffic, so the plain attribute assignment is safe
        (single-threaded, pre-traffic) without the hot-swap lock the
        provider-registry seams use.

        Args:
            project_cost_repo: Per-project aggregate the ceiling reads.
            claim_seen_repo: Durable dedup for the aggregate increment.
            cost_record_repo: Append-only store for the records
                themselves. Until it was wired, every record lived only
                in this process's memory: a restart lost the window a
                ceiling is enforced over, and every deliverable receipt
                reported zero spend.
        """
        self._project_cost_repo = project_cost_repo
        self._claim_seen_repo = claim_seen_repo
        self._cost_record_repo = cost_record_repo

    def bind_billing_model_resolver(
        self, resolver: BillingModelResolver | None
    ) -> None:
        """Bind where the ledger asks how a provider charges.

        Bound after construction, like the durable repos: the tracker is built
        at the synchronous construction phase and the provider registry is not
        available until wiring. Without a resolver a record keeps whatever it
        was constructed with, which is how the trackerless test harness runs.

        Args:
            resolver: The declaration source, or ``None`` to leave the ledger
                taking each record's own word for it.
        """
        self._billing_model_resolver = resolver

    def _with_billing_model(self, cost_record: CostRecord) -> CostRecord:
        """Return *cost_record* carrying its connection's declared billing.

        The connection's configuration is the single owner of how it charges,
        so a record arriving with a different claim is corrected rather than
        believed: otherwise a caller could make unmeasurable spend read as
        measurable simply by asserting it, which is the failure being fixed.

        Returns:
            The record, carrying the resolved billing model.
        """
        if self._billing_model_resolver is None:
            return cost_record
        resolved = self._billing_model_resolver.billing_model_for(cost_record.provider)
        if resolved is cost_record.billing_model:
            return cost_record
        return cost_record.model_copy(update={"billing_model": resolved})

    async def hydrate_from_durable(self) -> int:
        """Refill the in-memory window from the durable record store.

        The window is what every spend summary and every ceiling reads,
        and it starts empty on each boot. Bounded by the same 168-hour
        window the pruner enforces, so hydration restores exactly what a
        long-running process would still be holding.

        The read is paged rather than issued as one large query:
        repositories clamp ``limit`` to ``MAX_LIST_LIMIT`` inside
        ``validate_pagination_args`` without telling the caller, so a
        single whole-window request comes back as its newest page and the
        ceiling is enforced over a fraction of the spend.

        Best-effort: a read failure leaves the tracker empty and logs,
        because a cold window under-reports spend while a failed boot
        reports none at all.

        Returns:
            How many records were restored.
        """
        if self._cost_record_repo is None:
            return 0
        # Bound to a non-optional local because the closure below outlives
        # the narrowing of the attribute it reads.
        durable: CostRecordRepository = self._cost_record_repo
        since = self._clock.now() - timedelta(hours=_COST_WINDOW_HOURS)
        spec = CostRecordFilterSpec(since=since)
        pages: list[Sequence[CostRecord]] = []
        try:
            pages.extend(
                [
                    page
                    async for page in paginate(
                        lambda limit, offset: durable.query(
                            spec, limit=limit, offset=offset
                        ),
                        page_size=MAX_LIST_LIMIT,
                    )
                ]
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- a cold window under-reports spend;
            # failing the boot over it reports none at all.
            reraise_critical(exc)
            logger.warning(
                BUDGET_HYDRATION_FAILED,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return 0
        restored = await self._absorb_hydrated_pages(pages)
        logger.info(BUDGET_HYDRATED, restored=restored)
        return restored

    async def _absorb_hydrated_pages(
        self, pages: Sequence[Sequence[CostRecord]]
    ) -> int:
        """Merge a paged durable read into the in-memory window.

        Walked oldest-first, which the durable read is not: it orders
        ``timestamp DESC``. Two things depend on that reversal.
        ``get_records`` documents insertion order as oldest-first, and the
        claim LRU evicts from its head, so replaying a newest-first read
        verbatim would make the newest claim the least-recently-used one
        and a capacity trim would drop exactly the claims a redelivery
        repeats.

        A claim already held is skipped rather than appended: ``record()``
        writes its durable row before its in-memory one, so a hydration
        overlapping a live record sees the same claim from both sides and
        counting it twice can trip a hard stop that never happened.

        Args:
            pages: Durable pages in the read's newest-first order.

        Returns:
            How many records were newly added to the window.
        """
        restored = 0
        async with self._get_lock():
            for page in reversed(pages):
                for record in reversed(page):
                    if (
                        record.claim_id in self._seen_claims
                        or record.claim_id in self._inflight_claims
                    ):
                        continue
                    self._records.append(record)
                    self._promote_seen_claim(record.claim_id)
                    restored += 1
        return restored

    async def record(self, cost_record: CostRecord) -> None:
        """Append a cost record.

        Claim reservation and the in-memory append each run under
        ``_lock``. Between them, ``_durable_increment_if_unseen`` is
        awaited so a project record's durable aggregate is updated in
        one transaction before the record becomes visible in memory.
        A transient durable failure is fail-open (logged at WARNING) so
        it never blocks a legitimate first record.

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
        self._reject_mixed_currency(cost_record)
        # Stamped before the claim is reserved, so the durable append, the
        # in-memory window and the dedup ledger all carry the same record.
        cost_record = self._with_billing_model(cost_record)
        if not await self._reserve_claim(cost_record):
            return
        # Durable restart-survival guard, applied atomically. The
        # in-memory LRU above is empty after a crash/OOM/container
        # restart, so a JetStream redelivery of an already-billed
        # record would otherwise pass the memory check and re-increment
        # the durable aggregate. ``increment_if_unseen`` records the
        # dedup row and increments the aggregate in ONE transaction, so
        # a crash between the two can never leave the aggregate
        # incremented without its dedup row. A duplicate returns
        # ``was_new=False`` and skips the increment. The in-flight
        # reservation is held across this DB call, so any failure or
        # cancellation must release it -- ``except Exception`` would
        # miss CancelledError (timeout / shutdown), so catch
        # BaseException and re-raise.
        try:
            was_new = await self._durable_increment_if_unseen(cost_record)
        except BaseException:
            # Release the reservation WITHOUT awaiting the lock. Acquiring it
            # here opens a cancellation window (a timeout cancel racing a
            # shutdown cancel) in which the ``await`` is interrupted before the
            # discard runs, permanently leaking the claim into
            # ``_inflight_claims`` so every JetStream redelivery of it is
            # deduped away. ``set.discard`` is atomic on the single-threaded
            # event loop and no locked region iterates ``_inflight_claims``, so
            # a lockless discard of this call's own claim is race-free.
            self._inflight_claims.discard(cost_record.claim_id)
            raise
        if not was_new:
            await self._complete_duplicate(cost_record)
            return
        await self._finalise_new_record(cost_record)

    def _reject_mixed_currency(self, cost_record: CostRecord) -> None:
        """Refuse a record denominated in another currency than the budget.

        Checked first: it is synchronous, has no in-flight state to roll
        back, and a mismatch is a hard caller-contract violation that must
        surface BEFORE a claim slot is reserved. The per-project
        same-currency invariant is enforced by
        ``ProjectCostAggregateRepository.increment``, so no tracker-side pin
        is required there.

        Args:
            cost_record: The record being ingested.

        Raises:
            MixedCurrencyAggregationError: The currencies disagree.
        """
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

    async def _reserve_claim(self, cost_record: CostRecord) -> bool:
        """Reserve the claim, or report it as one already being handled.

        The idempotency fast path. ``_lock`` protects both
        ``_inflight_claims`` (reservations in progress) and ``_seen_claims``
        (the bounded LRU of finalised entries). Both are checked under the
        lock, then the claim is either deduped or reserved so a concurrent
        second call sees the entry and dedupes immediately. Keeping
        in-flight separate from the LRU stops the capacity trim popping a
        still-running reservation, which would let a duplicate slip past the
        membership check.

        Args:
            cost_record: The record being ingested.

        Returns:
            Whether the caller now holds the reservation and should proceed.
        """
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
                return False
            self._inflight_claims.add(cost_record.claim_id)
        return True

    async def _complete_duplicate(self, cost_record: CostRecord) -> None:
        """Finish what a redelivery of an already-counted claim can finish.

        Args:
            cost_record: The redelivered record.
        """
        # The aggregate already counts this claim, and its dedup row
        # committed in the same transaction, so no redelivery can ever
        # re-increment it. What a redelivery CAN still be missing is the
        # record itself: an append cancelled after that commit leaves the
        # claim counted and unrecorded, and this branch is the only place
        # a later delivery of it lands. The insert is idempotent on
        # ``(claim_id, timestamp)``, so completing it costs one no-op
        # insert when the row is already there.
        try:
            recorded = await self._append_durable(cost_record)
            async with self._get_lock():
                # Promoted only once the record is durably present: the
                # LRU short-circuits ahead of this branch, so promoting a
                # claim whose row never landed would close the one door
                # left to it. Promoted BEFORE the release below, so the
                # membership check never sees it in neither set.
                if recorded:
                    self._promote_seen_claim(cost_record.claim_id)
        finally:
            # Released however this leaves, for the same reason the first
            # delivery releases in its own guard: a cancellation between
            # the reservation and the discard strands the claim for the
            # life of the process, and every later redelivery of it is
            # then deduped away as still in flight.
            self._inflight_claims.discard(cost_record.claim_id)
        logger.info(
            BUDGET_RECORD_DEDUPED,
            claim_id=cost_record.claim_id,
            agent_id=cost_record.agent_id,
            task_id=cost_record.task_id,
            provider=cost_record.provider,
            model=cost_record.model,
            cost=cost_record.cost,
            reason="durable",
        )

    async def _finalise_new_record(self, cost_record: CostRecord) -> None:
        """Record a first delivery durably, then admit it to the window.

        Every await from here to the promotion is inside the guard, for the
        same reason the reservation is: a cancellation between the
        reservation and the discard strands the claim in
        ``_inflight_claims`` for the life of the process, and every later
        redelivery of it is then deduped away as still in flight. The record
        is retryable; a reservation nothing will ever release is not.

        Args:
            cost_record: The accepted record, holding its own reservation.
        """
        try:
            # Durable before in-memory: the record is what a receipt reads and
            # what a restart rehydrates from, so a record that exists only in
            # this process's memory is spend nothing else can see.
            recorded = await self._append_durable(cost_record)

            async with self._get_lock():
                # Promote the reservation to a finalised LRU entry under
                # the lock so the membership check above never observes
                # a gap where the claim is in neither set. Eviction only
                # affects ``_seen_claims``, so still-running reservations
                # in ``_inflight_claims`` are untouched.
                self._inflight_claims.discard(cost_record.claim_id)
                self._records.append(cost_record)
                # Withheld only when a redelivery could still finish the job:
                # the LRU short-circuits ahead of the durable path, so a claim
                # left out of it comes back through the duplicate branch and
                # retries the append there. Without durable dedup the LRU is
                # the only dedup there is, and withholding would double the
                # window rather than retry anything, so the drop stands as
                # logged.
                if recorded or not self._has_durable_dedup(cost_record):
                    self._promote_seen_claim(cost_record.claim_id)
                logger.info(
                    BUDGET_RECORD_ADDED,
                    agent_id=cost_record.agent_id,
                    model=cost_record.model,
                    cost=cost_record.cost,
                )
        except BaseException:
            # Lockless and idempotent: ``set.discard`` is atomic on the
            # single-threaded event loop and no locked region iterates
            # ``_inflight_claims``, so releasing this call's own reservation
            # cannot race the promotion that may already have run.
            self._inflight_claims.discard(cost_record.claim_id)
            raise

    def _has_durable_dedup(self, cost_record: CostRecord) -> bool:
        """Whether a redelivery of this record would be recognised durably.

        Only then is it safe to keep a claim out of the in-memory LRU after a
        dropped append: the duplicate branch recognises it, retries the
        record, and cannot re-bill the aggregate. Without a durable dedup row
        the LRU is the whole of the dedup, so a withheld claim would be
        counted twice instead of retried once.

        Args:
            cost_record: The record whose redelivery is in question.

        Returns:
            Whether the durable dedup row exists for this record.
        """
        return (
            self._project_cost_repo is not None
            and self._claim_seen_repo is not None
            and cost_record.project_id is not None
        )

    async def _append_durable(self, cost_record: CostRecord) -> bool:
        """Persist the record itself, best-effort but not silently.

        Fail-open for the same reason the aggregate increment is: losing a
        receipt must not lose the call it describes. A transient storage
        failure is retried first, because the record is what a restart
        rehydrates the ceiling from and the unique claim key makes the retry
        harmless. What survives the retry is a dropped record: one is a gap,
        but a run of them means spend is not being recorded at all, which is
        the exact failure this path exists to prevent, so past
        :data:`_PERSIST_FAILURE_ESCALATION_STREAK` it stops being a WARNING
        nobody reads.

        Args:
            cost_record: The accepted record to append durably.

        Returns:
            Whether the record is durably recorded. No durable store is
            ``True``: there is nowhere for it to be missing from.
        """
        repo = self._cost_record_repo
        if repo is None:
            return True
        try:
            await self._durable_retry.execute(
                lambda: repo.append(cost_record),
                claim_id=cost_record.claim_id,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            # lint-allow: swallow-ok -- the record is already accepted; a
            # storage blip must not fail the call it is describing.
            reraise_critical(exc)
            self._note_persist_failure(cost_record, exc)
            return False
        self._note_persist_success()
        return True

    def _note_persist_failure(self, cost_record: CostRecord, exc: Exception) -> None:
        """Log one dropped record, escalating once dropping them is a pattern.

        Args:
            cost_record: The record that did not reach the durable store.
            exc: What stopped it.
        """
        self._consecutive_persist_failures += 1
        recording = (
            self._consecutive_persist_failures < _PERSIST_FAILURE_ESCALATION_STREAK
        )
        log_fn = logger.warning if recording else logger.error
        log_fn(
            BUDGET_RECORD_PERSIST_FAILED,
            claim_id=cost_record.claim_id,
            agent_id=cost_record.agent_id,
            task_id=cost_record.task_id,
            project_id=cost_record.project_id,
            cost=cost_record.cost,
            currency=cost_record.currency,
            timestamp=cost_record.timestamp.isoformat(),
            consecutive_failures=self._consecutive_persist_failures,
            spend_recorded=recording,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )

    def _note_persist_success(self) -> None:
        """Clear the streak, announcing recovery only if there was one."""
        if self._consecutive_persist_failures >= _PERSIST_FAILURE_ESCALATION_STREAK:
            logger.info(
                BUDGET_RECORD_PERSIST_RECOVERED,
                dropped_records=self._consecutive_persist_failures,
            )
        self._consecutive_persist_failures = 0

    def _promote_seen_claim(self, claim_id: str) -> None:
        """Insert/refresh ``claim_id`` in the bounded in-memory dedup LRU.

        Caller holds ``_lock``. Trims the head on capacity overflow so a
        misbehaving caller spamming unique claim ids cannot grow the LRU
        without bound; in-flight reservations live in a separate set so
        the trim never evicts a still-running claim.
        """
        self._seen_claims[claim_id] = None
        self._seen_claims.move_to_end(claim_id)
        while len(self._seen_claims) > self._claim_lru_capacity:
            self._seen_claims.popitem(last=False)

    async def _durable_increment_if_unseen(self, cost_record: CostRecord) -> bool:
        """Atomically dedup + increment the durable aggregate.

        When both the aggregate and dedup repos are wired, records the
        dedup row and increments the project aggregate in a single
        repository transaction so the aggregate and its dedup row are
        committed together. When only the aggregate repo is wired (no
        dedup store), falls back to a plain increment with no durable
        dedup -- the in-memory LRU still guards same-process duplicates.

        Fail-open on a transient DB error: returns ``True`` (treat as a
        new record and proceed to the in-memory append) so a blip never
        blocks a legitimate first record. Returns ``True`` (no durable
        aggregate) when no aggregate repo or project scope is
        configured. ``MixedCurrencyAggregationError`` is a
        caller-contract violation and propagates.

        Returns:
            ``True`` when the record is new and should be appended;
            ``False`` when the durable store already recorded the claim.

        Raises:
            MixedCurrencyAggregationError: On a currency-pin mismatch.
        """
        if self._project_cost_repo is None or cost_record.project_id is None:
            return True
        try:
            if self._claim_seen_repo is None:
                # No durable dedup store: plain increment, no claim row.
                await self._project_cost_repo.increment(
                    cost_record.project_id,
                    cost_record.cost,
                    cost_record.input_tokens,
                    cost_record.output_tokens,
                    currency=cost_record.currency,
                )
                return True
            _, was_new = await self._project_cost_repo.increment_if_unseen(
                cost_record.project_id,
                cost_record.cost,
                cost_record.input_tokens,
                cost_record.output_tokens,
                currency=cost_record.currency,
                claim_id=cost_record.claim_id,
                now=self._clock.now(),
                ttl_seconds=self._claim_seen_ttl_seconds,
            )
        except MixedCurrencyAggregationError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            # The aggregation failed, which is wider than the dedup mark: this
            # handler also covers the plain-increment branch above, where no
            # claim is marked at all.
            logger.warning(
                BUDGET_PROJECT_COST_AGGREGATION_FAILED,
                claim_id=cost_record.claim_id,
                project_id=cost_record.project_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return True
        else:
            logger.debug(
                BUDGET_PROJECT_COST_AGGREGATED,
                project_id=cost_record.project_id,
                claim_id=cost_record.claim_id,
                cost=cost_record.cost,
                was_new=was_new,
            )
            return was_new

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

    async def get_qualified_total(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> QualifiedTotal:
        """Return a window's money total and what that total measures.

        The two answers are produced together, from one snapshot, because
        a money total is only a measure of usage where the provider bills
        per token: a caller reading the number alone cannot tell a zero
        meaning "nothing was spent" from a zero meaning "money never
        measured what was". Asked as two calls they take two snapshots
        under two lock acquisitions, and a record recorded in between
        yields a total that includes unmeasurable spend under a verdict
        saying every row was metered.

        Args:
            start: Inclusive lower bound on ``timestamp``.
            end: Exclusive upper bound on ``timestamp``.

        Returns:
            The rounded total and the measurability of the same rows.
            An empty window is ``MEASURED``: nothing was spent and nothing
            was hidden.

        Raises:
            ValueError: If both *start* and *end* are given and
                ``start >= end``.
        """
        _validate_time_range(start, end)
        snapshot = await self._snapshot()
        filtered = _filter_records(snapshot, start=start, end=end)
        return QualifiedTotal(
            cost=_aggregate(filtered).cost,
            measurability=measurability_of(tuple(r.billing_model for r in filtered)),
        )

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
        prompt_class_id: NotBlankStr | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        limit: int = DEFAULT_LIST_LIMIT,
        offset: int = 0,
    ) -> tuple[CostRecord, ...]:
        """Return a bounded page of filtered cost records.

        Returns one ``limit``-sized page of records matching the
        filters in insertion order (oldest-first), so a cursor walk is
        repeatable. Callers needing every matching record drain
        successive pages via
        :func:`synthorg.budget.tracker_protocol.collect_all_records`.

        Args:
            agent_id: Filter by agent.
            task_id: Filter by task.
            provider: Filter by provider name.
            prompt_class_id: Filter by prompt purpose id.
            start: Inclusive lower bound on ``timestamp``.
            end: Exclusive upper bound on ``timestamp``.
            limit: Maximum records to return.
            offset: Records to skip from the head of the ordering.

        Returns:
            Immutable tuple of matching cost records (one page).

        Raises:
            ValueError: If both *start* and *end* are given and
                ``start >= end``.
            QueryError: If ``limit`` / ``offset`` fail the shared
                pagination bounds/type checks.
        """
        _validate_time_range(start, end)
        # Reuse the shared validator the persistence repositories use:
        # it rejects bool/non-int, enforces limit>=1 / offset>=0, logs a
        # structured warning, and clamps to the repository page bound, so
        # the in-memory tracker cannot diverge from durable validation.
        limit = validate_pagination_args(limit, offset, event=BUDGET_RECORDS_QUERIED)
        logger.debug(
            BUDGET_RECORDS_QUERIED,
            agent_id=agent_id,
            task_id=task_id,
            provider=provider,
            prompt_class_id=prompt_class_id,
            start=start,
            end=end,
        )
        snapshot = await self._snapshot()
        matched = _filter_records(
            snapshot,
            agent_id=agent_id,
            task_id=task_id,
            provider=provider,
            prompt_class_id=prompt_class_id,
            start=start,
            end=end,
        )
        return matched[offset : offset + limit]

    async def collect_records(
        self,
        *,
        agent_id: str | None = None,
        task_id: str | None = None,
        provider: NotBlankStr | None = None,
        prompt_class_id: NotBlankStr | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> tuple[CostRecord, ...]:
        """Return every matching record from ONE atomic snapshot.

        Unlike a paginated :meth:`get_records` walk, this filters a single
        ``_snapshot()`` so a concurrent prune cannot shift offsets and drop
        records mid-drain. The result is the full matching set (the same
        materialisation a page-walk accumulates), so it carries no extra
        memory cost over the prior drain.

        Args:
            agent_id: Filter by agent.
            task_id: Filter by task.
            provider: Filter by provider name.
            prompt_class_id: Filter by prompt purpose id.
            start: Inclusive lower bound on ``timestamp``.
            end: Exclusive upper bound on ``timestamp``.

        Returns:
            Immutable tuple of every matching record, oldest-first.

        Raises:
            ValueError: If both *start* and *end* are given and ``start >= end``.
        """
        _validate_time_range(start, end)
        snapshot = await self._snapshot()
        return _filter_records(
            snapshot,
            agent_id=agent_id,
            task_id=task_id,
            provider=provider,
            prompt_class_id=prompt_class_id,
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
        # Drop the lock so ``_get_lock`` re-creates it bound to the next test's
        # event loop. A session-scoped tracker reused across loop restarts would
        # otherwise hand back a lock bound to the closed loop and deadlock.
        self._lock = None
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
        # Re-drain until the set is empty: a new record task can be added (via
        # ``track_pending_record``) WHILE we await ``gather`` below, so a single
        # snapshot would miss it. ``difference_update`` removes the just-drained
        # tasks immediately (rather than waiting on the add_done_callback to
        # fire), so the loop converges once no new task arrives.
        results: list[BaseException | None] = []
        while self._pending_record_tasks:
            pending = tuple(self._pending_record_tasks)
            results.extend(await asyncio.gather(*pending, return_exceptions=True))
            self._pending_record_tasks.difference_update(pending)
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
