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
from collections import OrderedDict, defaultdict
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, NamedTuple

from synthorg.budget._tracker_helpers import (
    _aggregate,
    _filter_records,
    _validate_time_range,
)
from synthorg.budget.enums import BudgetAlertLevel
from synthorg.budget.errors import MixedCurrencyAggregationError
from synthorg.budget.spending_summary import (
    AgentSpending,
    DepartmentSpending,
)
from synthorg.constants import BUDGET_ROUNDING_PRECISION
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.budget import (
    BUDGET_AGENT_COST_QUERIED,
    BUDGET_DEPARTMENT_RESOLVE_FAILED,
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

if TYPE_CHECKING:
    from collections.abc import Callable

    from synthorg.budget.config import BudgetConfig
    from synthorg.budget.cost_record import CostRecord
    from synthorg.persistence.project_cost_aggregate_protocol import (
        ProjectCostAggregateRepository,
    )

from synthorg.core.types import NotBlankStr  # noqa: TC001 -- runtime use

logger = get_logger(__name__)

_COST_WINDOW_HOURS = 168  # 7 days
_AUTO_PRUNE_THRESHOLD = 100_000

#: Default capacity of the per-tracker LRU set used to dedupe
#: ``CostRecord.claim_id``.  10k entries is well above the steady-state
#: rate of a busy synthetic org (the 7-day record window itself is
#: bounded at ``_AUTO_PRUNE_THRESHOLD``) and bounds memory growth so
#: a misbehaving caller cannot fill the dedup set without bound.
_DEFAULT_CLAIM_LRU_CAPACITY = 10_000


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

    def __init__(
        self,
        *,
        budget_config: BudgetConfig | None = None,
        department_resolver: Callable[[str], str | None] | None = None,
        auto_prune_threshold: int = _AUTO_PRUNE_THRESHOLD,
        project_cost_repo: ProjectCostAggregateRepository | None = None,
        claim_lru_capacity: int = _DEFAULT_CLAIM_LRU_CAPACITY,
    ) -> None:
        if auto_prune_threshold < 1:
            msg = f"auto_prune_threshold must be >= 1, got {auto_prune_threshold}"
            raise ValueError(msg)
        if claim_lru_capacity < 1:
            msg = f"claim_lru_capacity must be >= 1, got {claim_lru_capacity}"
            raise ValueError(msg)
        self._records: list[CostRecord] = []
        self._lock: asyncio.Lock = asyncio.Lock()
        self._budget_config = budget_config
        self._department_resolver = department_resolver
        self._auto_prune_threshold = auto_prune_threshold
        self._project_cost_repo = project_cost_repo
        # Bounded LRU of claim_ids the tracker has already accepted.
        # Stored as ``OrderedDict[str, None]`` so re-submission moves
        # the key to the tail in O(1) and the head can be popped on
        # capacity overflow without scanning.
        self._seen_claims: OrderedDict[str, None] = OrderedDict()
        self._claim_lru_capacity = claim_lru_capacity
        logger.debug(
            BUDGET_TRACKER_CREATED,
            has_budget_config=budget_config is not None,
            has_department_resolver=department_resolver is not None,
            has_project_cost_repo=project_cost_repo is not None,
            claim_lru_capacity=claim_lru_capacity,
        )

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
        """
        # Idempotency fast-path. We hold the lock so a concurrent
        # second call on the same claim_id can't race past us into the
        # currency check + append; the lock is released for the rest
        # of the (potentially-blocking) flow once the LRU has been
        # consulted.
        async with self._lock:
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

        if (
            self._budget_config is not None
            and cost_record.currency != self._budget_config.currency
        ):
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
        # Per-project same-currency invariant is enforced by the
        # repository (``ProjectCostAggregateRepository.increment``)
        # which raises ``MixedCurrencyAggregationError`` when the
        # incoming currency differs from the project's pinned
        # currency.  No tracker-side pin is required.

        # Run the durable aggregate update FIRST -- a
        # ``MixedCurrencyAggregationError`` from the per-project pin
        # must surface before the in-memory list is mutated.  Appending
        # only on success closes the concurrency window where a
        # ``_snapshot()`` call could observe the entry between an
        # append and a rollback.  DB I/O runs outside ``_lock`` so
        # concurrent in-memory readers/writers don't block on it.
        await self._update_project_aggregate(cost_record)

        async with self._lock:
            self._records.append(cost_record)
            # Mark the claim as seen so a JetStream redelivery / retry
            # of the same record cannot double-bill. Eviction keeps
            # memory bounded; once a claim ages out, a re-submission
            # is treated as fresh, which is the documented best-effort
            # contract.
            self._seen_claims[cost_record.claim_id] = None
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
            now: Reference time.  Defaults to current UTC time.

        Returns:
            Number of records removed.
        """
        ref = now or datetime.now(UTC)
        cutoff = ref - timedelta(hours=_COST_WINDOW_HOURS)
        async with self._lock:
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
        _validate_time_range(start, end)
        logger.debug(BUDGET_TOTAL_COST_QUERIED, start=start, end=end)
        snapshot = await self._snapshot()
        filtered = _filter_records(snapshot, start=start, end=end)
        return _aggregate(filtered).cost

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
        async with self._lock:
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
        """
        cleared_count = len(self._records)
        self._records.clear()
        logger.info(BUDGET_TRACKER_CLEARED, cleared_count=cleared_count)

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
        except MemoryError, RecursionError:
            raise
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
        except Exception:
            logger.warning(
                BUDGET_PROJECT_COST_AGGREGATION_FAILED,
                project_id=cost_record.project_id,
                cost=cost_record.cost,
                exc_info=True,
            )

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
                current UTC time.
        """
        async with self._lock:
            if len(self._records) > self._auto_prune_threshold:
                ref = now or datetime.now(UTC)
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
        """Remove records older than *cutoff*.  Caller must hold ``_lock``."""
        if not self._records:
            return 0
        before = len(self._records)
        self._records = [r for r in self._records if r.timestamp >= cutoff]
        return before - len(self._records)

    def _build_dept_spendings(
        self,
        agent_spendings: list[AgentSpending],
    ) -> list[DepartmentSpending]:
        """Aggregate per-department spending from agent spendings.

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
            currencies = {s.currency for s in spends if s.currency is not None}
            if len(currencies) > 1:
                msg = (
                    f"Department {dname!r} has agent spendings in "
                    f"different currencies: {sorted(currencies)}"
                )
                raise MixedCurrencyAggregationError(
                    msg,
                    currencies=frozenset(currencies),
                )
            dept_currency = next(iter(currencies)) if currencies else None
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

    def _build_budget_context(
        self,
        total_cost: float,
    ) -> tuple[float, float, BudgetAlertLevel]:
        """Compute budget monthly, used percentage, and alert level."""
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

    def _compute_alert_level(self, used_pct: float) -> BudgetAlertLevel:
        """Determine alert level from the rounded budget percentage."""
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

    def _resolve_department(self, agent_id: str) -> str | None:
        """Resolve agent to department, logging resolver errors."""
        if self._department_resolver is None:
            return None
        try:
            return self._department_resolver(agent_id)
        except MemoryError, RecursionError:
            raise
        except Exception as exc:
            logger.warning(
                BUDGET_DEPARTMENT_RESOLVE_FAILED,
                agent_id=agent_id,
                error=safe_error_description(exc),
                error_type=type(exc).__qualname__,
            )
            return None
