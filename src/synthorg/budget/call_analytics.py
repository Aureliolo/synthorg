# module-kind: service
"""Per-call analytics aggregation and alerting service."""

import asyncio
import math
from collections import Counter, defaultdict
from datetime import datetime
from typing import Final, NamedTuple

from synthorg.budget._alert_dispatch import dispatch_budget_alert
from synthorg.budget.call_analytics_config import (
    CallAnalyticsConfig,
    PromptClassAlertConfig,
)
from synthorg.budget.call_analytics_models import (
    AnalyticsAggregation,
    PromptClassBreakdown,
    PromptClassBreakdownRow,
    prompt_class_sort_key,
)
from synthorg.budget.category_analytics import (
    OrchestrationRatio,
    build_category_breakdown,
    compute_orchestration_ratio,
)
from synthorg.budget.cost_record import CostRecord
from synthorg.budget.currency import DEFAULT_CURRENCY, assert_currencies_match
from synthorg.budget.errors import MixedCurrencyAggregationError
from synthorg.budget.tracker_protocol import (
    CostTrackerProtocol,
    collect_all_records,
)
from synthorg.core.resilience import SlidingWindowEventLimiter
from synthorg.core.types import CapabilityLevel, NotBlankStr
from synthorg.llm.model_capability_policy import capability_for_purpose
from synthorg.llm.prompt_purpose import PromptPurposeId
from synthorg.notifications.dispatcher import NotificationDispatcher
from synthorg.observability import get_logger
from synthorg.observability.events.analytics import (
    ANALYTICS_AGGREGATION_COMPUTED,
    ANALYTICS_BREAKDOWN_COMPUTED,
    ANALYTICS_BREAKDOWN_MIXED_CURRENCY,
    ANALYTICS_CAPABILITY_LOOKUP_FAILED,
    ANALYTICS_PROMPT_CLASS_ALERT_DISPATCH_FAILED,
    ANALYTICS_PROMPT_CLASS_COST_ALERT,
    ANALYTICS_PROMPT_CLASS_LATENCY_ALERT,
    ANALYTICS_RETRY_ALERT_DISPATCH_FAILED,
    ANALYTICS_RETRY_RATE_ALERT,
    ANALYTICS_SERVICE_CREATED,
)

logger = get_logger(__name__)

# A call is considered "retried" once it has at least one retry attempt;
# zero-retry calls are excluded from the retry-rate numerator.
_MIN_RETRY_COUNT: Final[int] = 1

# 95th-percentile interpolation factor (NIST type-7 / linear-interpolation
# definition): pick the value at index 0.95 * (n - 1).
_PERCENTILE_INTERPOLATION_FACTOR: Final[float] = 0.95


class _RowBreach(NamedTuple):
    """A row's breached dimensions: deferred WARNING log specs + alert body.

    Built side-effect free so the caller can throttle both the logs and the
    dispatch behind a single cooldown admission.
    """

    logs: tuple[tuple[str, dict[str, object]], ...]
    body: str


class _AdmittedAlert(NamedTuple):
    """A per-purpose alert that passed the cooldown and awaits dispatch.

    ``handle`` is the limiter admission to refund if the dispatch fails;
    ``dispatcher`` is captured at admit time (only set when one is wired).
    """

    prompt_class_id: str
    body: str
    handle: object
    dispatcher: NotificationDispatcher


class CallAnalyticsService:
    """Aggregates per-call metrics and dispatches threshold alerts.

    Attributes are read-only after construction.  All public methods
    are coroutines.
    """

    def __init__(
        self,
        *,
        cost_tracker: CostTrackerProtocol,
        config: CallAnalyticsConfig,
        notification_dispatcher: NotificationDispatcher | None = None,
    ) -> None:
        """Create a CallAnalyticsService.

        Args:
            cost_tracker: Source of cost records.
            config: Analytics configuration.
            notification_dispatcher: Optional dispatcher for alerts.
        """
        self._tracker = cost_tracker
        self._config = config
        self._dispatcher = notification_dispatcher
        # One alert per purpose per window so a frequently-polled dashboard
        # cannot turn a standing breach into a notification storm.
        self._alert_limiter = SlidingWindowEventLimiter(
            max_events=1,
            window_seconds=config.prompt_class_alerts.min_seconds_between_alerts,
        )
        logger.debug(
            ANALYTICS_SERVICE_CREATED,
            enabled=config.enabled,
            has_dispatcher=notification_dispatcher is not None,
        )

    async def get_aggregation(
        self,
        *,
        agent_id: str | None = None,
        task_id: str | None = None,
        provider: NotBlankStr | None = None,
        prompt_class_id: NotBlankStr | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> AnalyticsAggregation:
        """Compute aggregated analytics over cost records.

        Args:
            agent_id: Filter by agent.
            task_id: Filter by task.
            provider: Filter by provider name.
            prompt_class_id: Filter by prompt purpose id.
            start: Inclusive lower bound on timestamp.
            end: Exclusive upper bound on timestamp.

        Returns:
            Aggregated analytics over the matching records.
        """
        records = await collect_all_records(
            self._tracker,
            agent_id=agent_id,
            task_id=task_id,
            provider=provider,
            prompt_class_id=prompt_class_id,
            start=start,
            end=end,
        )
        # Derive the orchestration ratio from the same filtered snapshot the
        # counts come from, so every filter (provider, prompt_class_id, time
        # window) scopes both consistently. A separate tracker query would
        # ignore provider/prompt_class_id and read its own snapshot, yielding
        # an internally inconsistent aggregation.
        orchestration_ratio = compute_orchestration_ratio(
            build_category_breakdown(records),
            thresholds=self._config.orchestration_alerts,
        )
        agg = _build_aggregation(records, orchestration_ratio)
        logger.debug(
            ANALYTICS_AGGREGATION_COMPUTED,
            total_calls=agg.total_calls,
            retry_rate=agg.retry_rate,
        )
        return agg

    async def get_prompt_class_breakdown(
        self,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> PromptClassBreakdown:
        """Aggregate cost + latency + quality per prompt class.

        Records with no ``prompt_class_id`` (per-task / agent-execution calls
        that carry no registered system prompt purpose) group into a single
        ``None`` row rather than being dropped, so the breakdown still sums to
        the headline total.

        Args:
            start: Inclusive lower bound on timestamp.
            end: Exclusive upper bound on timestamp.

        Returns:
            One row per prompt class, sorted by ``prompt_class_id`` with the
            no-prompt-class row first.
        """
        records = await collect_all_records(self._tracker, start=start, end=end)
        breakdown = _build_prompt_class_breakdown(records)
        logger.debug(ANALYTICS_BREAKDOWN_COMPUTED, row_count=len(breakdown.rows))
        # The by-purpose view is the only live read path, so the per-purpose
        # cost / latency alert thresholds are evaluated here; both default to
        # off, so an unconfigured deployment dispatches nothing.
        await self.check_prompt_class_alerts(breakdown)
        return breakdown

    async def check_alerts(
        self,
        records: tuple[CostRecord, ...],
    ) -> None:
        """Check alert thresholds and dispatch notifications if crossed.

        Args:
            records: Cost records to evaluate.
        """
        if not self._config.enabled or not records:
            return

        total = len(records)
        retried = sum(
            1
            for r in records
            if r.retry_count is not None and r.retry_count >= _MIN_RETRY_COUNT
        )
        retry_rate = retried / total

        if retry_rate > self._config.retry_alerts.warn_rate:
            logger.warning(
                ANALYTICS_RETRY_RATE_ALERT,
                retry_rate=retry_rate,
                warn_rate=self._config.retry_alerts.warn_rate,
            )
            if self._dispatcher is not None:
                warn_rate = self._config.retry_alerts.warn_rate
                await dispatch_budget_alert(
                    self._dispatcher,
                    title="High retry rate alert",
                    body=(
                        f"Retry rate {retry_rate:.1%} exceeds warning "
                        f"threshold {warn_rate:.1%}."
                    ),
                    on_failure_event=ANALYTICS_RETRY_ALERT_DISPATCH_FAILED,
                )

    async def check_prompt_class_alerts(
        self,
        breakdown: PromptClassBreakdown,
    ) -> None:
        """Dispatch per-purpose cost / latency alerts for crossed thresholds.

        Both thresholds are opt-in; a deployment that configures neither
        dispatches nothing. A row's p95 latency is evaluated only when the
        purpose reported latency at all. Each purpose re-alerts at most once
        per ``min_seconds_between_alerts`` window, so repeated dashboard reads
        cannot storm the notification sinks; a row breaching both dimensions
        produces a single combined notification dispatched concurrently with
        the other purposes' alerts.

        Args:
            breakdown: The per-prompt-class breakdown to evaluate.
        """
        if not self._config.enabled:
            return
        alerts = self._config.prompt_class_alerts
        if alerts.cost_warn is None and alerts.p95_latency_warn_ms is None:
            return
        admitted: list[_AdmittedAlert] = []
        for row in breakdown.rows:
            if row.prompt_class_id is None:
                # The no-prompt-class row aggregates every promptless call in
                # the window, so a breach on it names no purpose an operator
                # could act on. It stays in the breakdown and out of alerting.
                continue
            breach = self._row_breach(row, alerts)
            if breach is None:
                continue
            handle = await self._alert_limiter.take(row.prompt_class_id)
            if handle is None:
                # Within this purpose's cooldown window: emit neither the breach
                # logs nor a dispatch, so a polled dashboard does not re-alert.
                continue
            # Logging is gated by the cooldown admission (not by _row_breach,
            # which is side-effect free) so the WARNING stream is throttled the
            # same as the dispatch, including on the dispatcher-is-None path.
            for event, fields in breach.logs:
                logger.warning(event, **fields)
            if self._dispatcher is None:
                # Breach logged once per window; there is no sink to dispatch to.
                continue
            admitted.append(
                _AdmittedAlert(
                    row.prompt_class_id, breach.body, handle, self._dispatcher
                )
            )
        if not admitted:
            return
        async with asyncio.TaskGroup() as tg:
            for alert in admitted:
                _ = tg.create_task(self._dispatch_admitted(alert))

    def _row_breach(
        self,
        row: PromptClassBreakdownRow,
        alerts: PromptClassAlertConfig,
    ) -> _RowBreach | None:
        """Build the breach record for a row (side-effect free, no logging).

        The per-dimension WARNING log specs stay separate (so spend and latency
        regressions stay queryable), but a single body backs one notification
        per row. The caller emits the logs only after the cooldown admits the
        row.

        Returns:
            A :class:`_RowBreach`, or ``None`` when the row breaches neither
            ceiling (so the caller skips it without consuming a cooldown slot).
        """
        logs: list[tuple[str, dict[str, object]]] = []
        parts: list[str] = []
        if alerts.cost_warn is not None and row.total_cost > alerts.cost_warn:
            logs.append(
                (
                    ANALYTICS_PROMPT_CLASS_COST_ALERT,
                    {
                        "prompt_class_id": row.prompt_class_id,
                        "total_cost": row.total_cost,
                        "cost_warn": alerts.cost_warn,
                    },
                )
            )
            parts.append(
                f"cost {row.total_cost:.4f} {row.currency} exceeds the warning "
                f"ceiling {alerts.cost_warn:.4f}"
            )
        p95 = row.p95_latency_ms
        if (
            alerts.p95_latency_warn_ms is not None
            and p95 is not None
            and p95 > alerts.p95_latency_warn_ms
        ):
            logs.append(
                (
                    ANALYTICS_PROMPT_CLASS_LATENCY_ALERT,
                    {
                        "prompt_class_id": row.prompt_class_id,
                        "p95_latency_ms": p95,
                        "p95_latency_warn_ms": alerts.p95_latency_warn_ms,
                    },
                )
            )
            parts.append(
                f"p95 latency {p95:.0f}ms exceeds the warning ceiling "
                f"{alerts.p95_latency_warn_ms:.0f}ms"
            )
        if not parts:
            return None
        body = f"Prompt purpose {row.prompt_class_id!r}: {'; '.join(parts)}."
        return _RowBreach(logs=tuple(logs), body=body)

    async def _dispatch_admitted(self, alert: _AdmittedAlert) -> None:
        """Dispatch one admitted alert; refund its slot on a swallowed failure."""
        dispatched = await dispatch_budget_alert(
            alert.dispatcher,
            title="High prompt-purpose cost / latency alert",
            body=alert.body,
            on_failure_event=ANALYTICS_PROMPT_CLASS_ALERT_DISPATCH_FAILED,
            prompt_class_id=alert.prompt_class_id,
        )
        if not dispatched:
            await self._alert_limiter.release(alert.prompt_class_id, alert.handle)


# ── Pure helpers ─────────────────────────────────────────────────────────────


def _build_aggregation(
    records: tuple[CostRecord, ...],
    orchestration_ratio: OrchestrationRatio,
) -> AnalyticsAggregation:
    """Build an AnalyticsAggregation from records.

    Args:
        records: Cost records to aggregate.
        orchestration_ratio: Pre-computed orchestration ratio.

    Returns:
        Populated AnalyticsAggregation.
    """
    total = len(records)

    success_count = sum(1 for r in records if r.success is True)
    failure_count = sum(1 for r in records if r.success is False)

    retried = sum(
        1
        for r in records
        if r.retry_count is not None and r.retry_count >= _MIN_RETRY_COUNT
    )
    retry_rate = retried / total if total > 0 else 0.0

    cache_reporting = [r for r in records if r.cache_hit is not None]
    cache_hit_count = sum(1 for r in cache_reporting if r.cache_hit is True)
    cache_hit_rate = cache_hit_count / len(cache_reporting) if cache_reporting else None

    latencies = [r.latency_ms for r in records if r.latency_ms is not None]
    avg_latency_ms = sum(latencies) / len(latencies) if latencies else None
    p95_latency_ms = _p95(latencies) if latencies else None

    return AnalyticsAggregation(
        total_calls=total,
        success_count=success_count,
        failure_count=failure_count,
        retry_count=retried,
        retry_rate=retry_rate,
        cache_hit_count=cache_hit_count,
        cache_hit_rate=cache_hit_rate,
        avg_latency_ms=avg_latency_ms,
        p95_latency_ms=p95_latency_ms,
        orchestration_ratio=orchestration_ratio,
        by_finish_reason=_finish_reason_counts(records),
    )


def _finish_reason_counts(
    records: tuple[CostRecord, ...],
) -> tuple[tuple[str, int], ...]:
    """Count records by finish reason, sorted by reason for stable output.

    Returns:
        Sorted ``(finish_reason, count)`` pairs over records that carry one.
    """
    reason_counts: Counter[str] = Counter(
        r.finish_reason.value for r in records if r.finish_reason is not None
    )
    return tuple(sorted(reason_counts.items()))


def _capability_for(prompt_class_id: str | None) -> CapabilityLevel | None:
    """Return the design capability for a purpose id, or None when unmapped.

    Returns:
        The capability rung, or ``None`` when ``prompt_class_id`` is absent or is
        not a registered ``PromptPurposeId`` (a historical id left by a
        renamed/removed purpose).
    """
    if prompt_class_id is None:
        return None
    try:
        purpose = PromptPurposeId(prompt_class_id)
    except ValueError:
        logger.warning(
            ANALYTICS_CAPABILITY_LOOKUP_FAILED,
            prompt_class_id=prompt_class_id,
            reason="unrecognised_purpose_id",
        )
        return None
    # A registered purpose is guaranteed a capability-policy entry by the
    # import-time guard in model_capability_policy, so a KeyError here is a
    # policy-map integrity failure: let it surface rather than masking it as
    # a null rung.
    return capability_for_purpose(purpose)


def _build_breakdown_row(
    prompt_class_id: str | None,
    records: list[CostRecord],
) -> PromptClassBreakdownRow:
    """Aggregate one prompt class's records into a breakdown row.

    Returns:
        The populated :class:`PromptClassBreakdownRow`.

    Raises:
        MixedCurrencyAggregationError: If the class's records span more than
            one currency (cost summation across currencies is rejected).
    """
    total = len(records)
    try:
        currency = assert_currencies_match(r.currency for r in records)
    except MixedCurrencyAggregationError:
        # assert_currencies_match logs the conflicting codes but not which
        # prompt class triggered them; add that so the 409 is greppable.
        logger.warning(
            ANALYTICS_BREAKDOWN_MIXED_CURRENCY,
            prompt_class_id=prompt_class_id,
        )
        raise

    retried = sum(
        1
        for r in records
        if r.retry_count is not None and r.retry_count >= _MIN_RETRY_COUNT
    )
    cache_reporting = [r for r in records if r.cache_hit is not None]
    cache_hit_rate = (
        sum(1 for r in cache_reporting if r.cache_hit is True) / len(cache_reporting)
        if cache_reporting
        else None
    )
    success_reporting = [r for r in records if r.success is not None]
    success_rate = (
        sum(1 for r in success_reporting if r.success is True) / len(success_reporting)
        if success_reporting
        else None
    )
    latencies = [r.latency_ms for r in records if r.latency_ms is not None]

    return PromptClassBreakdownRow(
        prompt_class_id=(
            NotBlankStr(prompt_class_id) if prompt_class_id is not None else None
        ),
        capability=_capability_for(prompt_class_id),
        total_cost=math.fsum(r.cost for r in records),
        currency=currency if currency is not None else DEFAULT_CURRENCY,
        call_count=total,
        input_tokens=sum(r.input_tokens for r in records),
        output_tokens=sum(r.output_tokens for r in records),
        avg_latency_ms=(sum(latencies) / len(latencies) if latencies else None),
        p95_latency_ms=_p95(latencies) if latencies else None,
        cache_hit_rate=cache_hit_rate,
        retry_rate=retried / total if total > 0 else 0.0,
        success_rate=success_rate,
    )


def _build_prompt_class_breakdown(
    records: tuple[CostRecord, ...],
) -> PromptClassBreakdown:
    """Group records by prompt class and build one row per class.

    Records with no ``prompt_class_id`` group into a single ``None`` row
    rather than being dropped: an embedding call wraps no system prompt, and
    a breakdown that silently omits it stops summing to the headline total,
    which is how subsystem spend went missing in the first place.

    Returns:
        A :class:`PromptClassBreakdown` with rows sorted by id, the
        no-prompt-class row first.
    """
    by_class: dict[str | None, list[CostRecord]] = defaultdict(list)
    for record in records:
        by_class[record.prompt_class_id].append(record)
    rows = tuple(
        _build_breakdown_row(prompt_class_id, group)
        for prompt_class_id, group in sorted(
            by_class.items(), key=lambda item: prompt_class_sort_key(item[0])
        )
    )
    return PromptClassBreakdown(rows=rows)


def _p95(values: list[float]) -> float:
    """Compute the 95th percentile via linear interpolation.

    Args:
        values: List of values (at least one element).

    Returns:
        95th-percentile value.
    """
    sorted_values = sorted(values)
    n = len(sorted_values)
    if n == 1:
        return sorted_values[0]
    index = _PERCENTILE_INTERPOLATION_FACTOR * (n - 1)
    lo = int(index)
    hi = lo + 1
    frac = index - lo
    if hi >= n:
        return sorted_values[-1]
    return sorted_values[lo] + frac * (sorted_values[hi] - sorted_values[lo])
