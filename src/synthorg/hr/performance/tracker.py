# module-kind: complex_service
"""Performance tracker service.

The task-metric ledger: every terminal task run is recorded here, and
snapshots roll those records into rolling windows and trends.

``quality_score`` on a record is the completion oracle's verdict,
stamped by :class:`TaskActivityObserver` at write time. The tracker
reads it and never derives one, so "how good was this work" keeps a
single owner.

One cohesive responsibility: track per-agent performance. Metric
recording, snapshot computation (windows + trends), and inflection
emission all read and mutate the same state under ``_metrics_lock``
(the ``_background_tasks`` set, the ``_closing`` flag, the
``_trend_direction_cache``); they form one single-threaded pipeline
that owns those invariants. A per-concern split would either duplicate
the lock + flags across modules or introduce a separate coordinator
that re-establishes the same boundary at a higher cost. The strategy
seams (window / trend / inflection sink) already isolate the pluggable
algorithms.
"""

import asyncio
import re
from datetime import UTC, datetime, timedelta
from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.engine.coordination.attribution import AgentContribution
from synthorg.hr.enums import TrendDirection
from synthorg.hr.performance.config import PerformanceConfig
from synthorg.hr.performance.inflection_protocol import InflectionSink
from synthorg.hr.performance.models import (
    AgentPerformanceSnapshot,
    TaskMetricRecord,
    TrendResult,
    WindowMetrics,
)
from synthorg.hr.performance.trend_protocol import TrendDetectionStrategy
from synthorg.hr.performance.window_protocol import MetricsWindowStrategy
from synthorg.hr.persistence_protocol import TaskMetricRepository
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.inflection import (
    PERF_INFLECTION_DETECTED,
    PERF_INFLECTION_EMISSION_FAILED,
)
from synthorg.observability.events.performance import (
    PERF_AGENT_FORGOTTEN,
    PERF_BACKGROUND_TASK_FAILED,
    PERF_INFLECTION_SINK_BIND_REJECTED,
    PERF_INFLECTION_SINK_BOUND,
    PERF_INFLECTION_SINK_CLEARED,
    PERF_METRIC_PERSIST_FAILED,
    PERF_METRIC_RECORDED,
    PERF_SNAPSHOT_COMPUTED,
    PERF_SNAPSHOT_FAILED,
    PERF_TRACKER_CLEARED,
    PERF_WINDOW_INSUFFICIENT_DATA,
)
from synthorg.persistence.agent_contribution_protocol import (
    AgentContributionRepository,
)

logger = get_logger(__name__)

# Upper bound on a single ``get_snapshots`` batch.  Each input id
# triggers a separate snapshot computation (scorers + window logic +
# trend detection); unbounded fan-out from a user-controllable caller
# would let a client burn arbitrary CPU on a single request.
MAX_BATCH_SNAPSHOTS_LOOKUP: Final[int] = 1024

# Bound the best-effort durable metric write: without it a hung backend
# stalls the save indefinitely and the fail-open WARNING below never
# fires, so the metric is neither persisted nor reported as lost.
_PERSIST_TIMEOUT_SECONDS: Final[float] = 5.0


# Quality scores are reported on a 0-10 axis; dividing by this normalises
# a human override score into the snapshot's [0, 1] human-feedback signal.
class PerformanceTracker:
    """Central service for recording and querying agent performance metrics.

    In-memory storage keyed by agent_id. Delegates windowing and trend
    detection to injected strategy implementations.

    When strategies are not provided, sensible defaults are constructed
    from ``PerformanceConfig``.

    Args:
        window_strategy: Strategy for computing rolling windows.
        trend_strategy: Strategy for detecting trends.
        config: Performance tracking configuration.
    """

    def __init__(
        self,
        *,
        window_strategy: MetricsWindowStrategy | None = None,
        trend_strategy: TrendDetectionStrategy | None = None,
        config: PerformanceConfig | None = None,
        inflection_sink: InflectionSink | None = None,
        task_metric_repo: TaskMetricRepository | None = None,
        contribution_repo: AgentContributionRepository | None = None,
    ) -> None:
        cfg = config or PerformanceConfig()
        self._config = cfg
        self._task_metric_repo = task_metric_repo
        self._contribution_repo = contribution_repo
        self._window_strategy = window_strategy or self._default_window(cfg)
        self._trend_strategy = trend_strategy or self._default_trend(cfg)
        self._inflection_sink = inflection_sink
        self._trend_direction_cache: dict[tuple[str, str, str], TrendDirection] = {}
        # Monotonic per-agent "forget" counter. Captured when an
        # inflection-emission task is scheduled and re-checked under the
        # lock before that task writes ``_trend_direction_cache``; a
        # ``forget_agent`` that bumps the counter in between makes the
        # stale task skip its writes so it cannot repopulate cache keys
        # for an agent that has since been forgotten.
        self._forget_generation: dict[str, int] = {}
        self._task_metrics: dict[str, list[TaskMetricRecord]] = {}
        self._contributions: dict[str, list[AgentContribution]] = {}
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._metrics_lock = asyncio.Lock()
        # Set to True while ``aclose()`` is draining so new background
        # tasks cannot be enqueued between the task-set snapshot and
        # the clear. Guarded by ``_metrics_lock`` on both read and
        # write sides.
        self._closing: bool = False

    @staticmethod
    def _default_window(cfg: PerformanceConfig) -> MetricsWindowStrategy:
        """Default window.

        Returns:
            Result of type ``MetricsWindowStrategy``.
        """
        from synthorg.hr.performance.multi_window_strategy import (  # noqa: PLC0415
            MultiWindowStrategy,
        )

        return MultiWindowStrategy(
            windows=tuple(str(w) for w in cfg.windows),
            min_data_points=cfg.min_data_points,
        )

    @staticmethod
    def _default_trend(cfg: PerformanceConfig) -> TrendDetectionStrategy:
        """Default trend.

        Returns:
            Result of type ``TrendDetectionStrategy``.
        """
        from synthorg.hr.performance.theil_sen_strategy import (  # noqa: PLC0415
            TheilSenTrendStrategy,
        )

        return TheilSenTrendStrategy(
            min_data_points=cfg.min_data_points,
            improving_threshold=cfg.improving_threshold,
            declining_threshold=cfg.declining_threshold,
        )

    def reset_for_test_sync(self) -> None:
        """Synchronously reset all recorded metrics for test isolation.

        Cancels pending background tasks via ``Task.cancel()`` but does
        **not** await them. This is the test-only sync entry point the
        sync test-reset fixture calls when no running event loop is
        available (mirroring ``ApprovalStore.reset_for_test_sync`` and
        ``reset_registry_for_test_sync``). Production code uses the async
        :meth:`aclear` / :meth:`aclose`, which is the sole public reset.
        """
        tasks_cancelled = len(self._background_tasks)
        task_metrics_cleared = len(self._task_metrics)
        contributions_cleared = len(self._contributions)
        trend_cache_cleared = len(self._trend_direction_cache)
        # Iterate over a snapshot: task done-callbacks remove from the
        # set, so iterating the live set would raise
        # ``RuntimeError: set changed size during iteration``.
        for t in list(self._background_tasks):
            t.cancel()
        self._background_tasks.clear()
        self._task_metrics.clear()
        self._contributions.clear()
        self._trend_direction_cache.clear()
        logger.info(
            PERF_TRACKER_CLEARED,
            tasks_cancelled=tasks_cancelled,
            task_metrics_cleared=task_metrics_cleared,
            contributions_cleared=contributions_cleared,
            trend_cache_cleared=trend_cache_cleared,
        )

    async def aclose(self) -> None:
        """Cancel and await all pending background tasks.

        Should be called during application shutdown to prevent
        ``RuntimeError: Task was destroyed but it is pending!``
        warnings.

        Sets ``_closing`` under ``_metrics_lock`` before snapshotting
        so a concurrent ``get_snapshot`` (which schedules under the same
        lock) refuses to enqueue new background tasks once shutdown has
        started. Without that gate a task scheduled right after the
        snapshot would survive aclose() with the result that the caller
        sees ``aclose() returned`` while a live inflection task
        keeps running and can still repopulate cache state.

        Raises:
            system_error: Raised when the relevant invariant fails.
        """
        async with self._metrics_lock:
            self._closing = True
            tasks = list(self._background_tasks)
            self._background_tasks.clear()
        for t in tasks:
            t.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        # Preserve system-error signals: ``_maybe_sample`` and
        # ``_do_emit_inflections`` explicitly re-raise MemoryError /
        # RecursionError (and other BaseException subclasses other
        # than CancelledError). Discarding them here would silently
        # mask OS-level failures; log unexpected non-cancellation
        # exceptions and re-raise the first BaseException seen so the
        # lifecycle layer can surface it.
        system_error: BaseException | None = None
        for result in results:
            if not isinstance(result, BaseException):
                continue
            if isinstance(result, asyncio.CancelledError):
                continue
            if isinstance(result, Exception):
                logger.warning(
                    PERF_BACKGROUND_TASK_FAILED,
                    error_type=type(result).__name__,
                    error=safe_error_description(result),
                )
                continue
            if system_error is None:
                system_error = result
        if system_error is not None:
            raise system_error

    async def aclear(self) -> None:
        """Async-safe reset of all recorded metrics.

        Acquires ``_metrics_lock`` so no recorder can observe a partial
        clear and no reader can race the mutation of ``_task_metrics``
        / ``_contributions`` / ``_trend_direction_cache``. Cancels
        pending background tasks *without* awaiting them (matches
        :meth:`clear` semantics) so the call is cheap in hot tests.

        Production callers that must drain outstanding tasks cleanly
        should call :meth:`aclose` instead.
        """
        async with self._metrics_lock:
            tasks_cancelled = len(self._background_tasks)
            task_metrics_cleared = len(self._task_metrics)
            contributions_cleared = len(self._contributions)
            trend_cache_cleared = len(self._trend_direction_cache)
            for t in list(self._background_tasks):
                t.cancel()
            self._background_tasks.clear()
            self._task_metrics.clear()
            self._contributions.clear()
            self._trend_direction_cache.clear()
            self._forget_generation.clear()
        logger.info(
            PERF_TRACKER_CLEARED,
            tasks_cancelled=tasks_cancelled,
            task_metrics_cleared=task_metrics_cleared,
            contributions_cleared=contributions_cleared,
            trend_cache_cleared=trend_cache_cleared,
        )

    async def forget_agent(self, agent_id: NotBlankStr) -> None:
        """Evict all in-memory metrics for a single departed agent.

        Without this hook the per-agent ``_task_metrics`` /
        ``_contributions`` dicts (and the ``_trend_direction_cache``
        keyed on ``agent_id``) accumulate across agent churn for the
        process lifetime, since records are only ever appended.
        Offboarding / deregistration calls this so a retired agent's
        footprint is reclaimed without clearing every other agent's
        metrics (unlike :meth:`aclear`).

        Args:
            agent_id: Identifier of the agent to forget.
        """
        agent_key = str(agent_id)
        async with self._metrics_lock:
            task_cleared = len(self._task_metrics.pop(agent_key, []))
            contrib_cleared = len(self._contributions.pop(agent_key, []))
            stale_trends = [
                key for key in self._trend_direction_cache if key[0] == agent_key
            ]
            for key in stale_trends:
                del self._trend_direction_cache[key]
            # Invalidate any inflection-emission task scheduled before this
            # forget so it cannot repopulate the cache keys just cleared.
            self._forget_generation[agent_key] = (
                self._forget_generation.get(agent_key, 0) + 1
            )
        logger.info(
            PERF_AGENT_FORGOTTEN,
            agent_id=agent_key,
            task_metrics_cleared=task_cleared,
            contributions_cleared=contrib_cleared,
            trend_cache_cleared=len(stale_trends),
        )

    async def record_task_metric(
        self,
        record: TaskMetricRecord,
    ) -> TaskMetricRecord:
        """Record a task completion metric.

        Args:
            record: The task metric record to store.

        Returns:
            The stored record.
        """
        async with self._metrics_lock:
            agent_key = str(record.agent_id)
            if agent_key not in self._task_metrics:
                self._task_metrics[agent_key] = []
            self._task_metrics[agent_key].append(record)

        logger.info(
            PERF_METRIC_RECORDED,
            agent_id=record.agent_id,
            task_id=record.task_id,
            is_success=record.is_success,
        )
        await self._persist_metric(
            self._task_metric_repo, record, agent_id=str(record.agent_id)
        )
        return record

    def attach_metric_repos(
        self,
        *,
        task_metric_repo: TaskMetricRepository,
        contribution_repo: AgentContributionRepository | None = None,
    ) -> None:
        """Attach the durable metric repos post-connect.

        The tracker is built at the synchronous construction phase before
        persistence is connected, so the durable repos are wired here once
        a backend exists. Called once at boot before traffic, so the plain
        attribute assignment is safe without a lock.
        """
        self._task_metric_repo = task_metric_repo
        if contribution_repo is not None:
            self._contribution_repo = contribution_repo

    async def _persist_metric(
        self,
        repo: TaskMetricRepository | None,
        record: TaskMetricRecord,
        *,
        agent_id: str,
    ) -> None:
        """Durably persist a metric record best-effort.

        The in-memory append is the source of truth for live queries;
        the durable write is a backstop so a restart does not silently
        discard recorded performance data. Fail-open: a write failure
        logs at WARNING but never surfaces to the caller, mirroring the
        cost-aggregate path.
        """
        if repo is None:
            return
        try:
            async with asyncio.timeout(_PERSIST_TIMEOUT_SECONDS):
                await repo.save(record)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                PERF_METRIC_PERSIST_FAILED,
                agent_id=agent_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def _persist_contribution(self, contribution: AgentContribution) -> None:
        """Durably append one contribution best-effort.

        Fail-open: a write failure logs at WARNING but never surfaces to
        the caller, mirroring :meth:`_persist_metric`.
        """
        if self._contribution_repo is None:
            return
        try:
            async with asyncio.timeout(_PERSIST_TIMEOUT_SECONDS):
                await self._contribution_repo.append(contribution)
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                PERF_METRIC_PERSIST_FAILED,
                agent_id=str(contribution.agent_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )

    async def record_coordination_contributions(
        self,
        contributions: tuple[AgentContribution, ...],
    ) -> None:
        """Store per-agent contributions from coordination.

        Args:
            contributions: Attribution records from a coordinated run.
        """
        async with self._metrics_lock:
            for contrib in contributions:
                agent_key = str(contrib.agent_id)
                self._contributions.setdefault(agent_key, []).append(contrib)

        # Durably persist contributions best-effort so the accumulator
        # survives restarts and is queryable for retrospective attribution
        # analytics (the in-memory list is otherwise discarded on restart).
        # Persist concurrently so a slow backend costs ~one shared timeout
        # window for the whole batch rather than one timeout per
        # contribution (``len * _PERSIST_TIMEOUT_SECONDS`` on the serial
        # path). Each ``_persist_contribution`` is fail-open, so a per-item
        # failure is logged without aborting the batch; only a re-raised
        # critical (MemoryError/RecursionError) propagates.
        # Skip the fan-out entirely when no repo is wired: every
        # ``_persist_contribution`` would early-return, so spawning a task
        # per contribution is pure scheduling overhead on the in-memory path.
        if contributions and self._contribution_repo is not None:
            async with asyncio.TaskGroup() as tg:
                # Fire-and-forget within the group: the TaskGroup awaits every
                # task on exit. Bind the handles to ``_`` so the discarded
                # tasks do not trip the unused-awaitable check.
                _ = [
                    tg.create_task(self._persist_contribution(contrib))
                    for contrib in contributions
                ]

        if contributions:
            logger.info(
                PERF_METRIC_RECORDED,
                contribution_count=len(contributions),
                avg_score=round(
                    sum(c.contribution_score for c in contributions)
                    / len(contributions),
                    3,
                ),
            )

    async def get_snapshots(
        self,
        agent_ids: tuple[NotBlankStr, ...],
        *,
        now: datetime | None = None,
    ) -> tuple[AgentPerformanceSnapshot | None, ...]:
        """Compute performance snapshots for a batch of agents.

        Order-preserving: the returned tuple has one entry per input
        id in the same order.  Entries are ``None`` when snapshot
        computation raises (e.g. insufficient data, strategy error).
        Single-agent log emissions are preserved so existing
        observability pipelines keep working.

        Args:
            agent_ids: Ordered tuple of agent identifiers.
            now: Reference time (defaults to current UTC time).

        Returns:
            Tuple of snapshots (or ``None`` on failure) in input order.

        Raises:
            ValueError: If ``len(agent_ids)`` exceeds
                ``MAX_BATCH_SNAPSHOTS_LOOKUP``.  Snapshot computation is
                O(N) in the batch size; an unbounded batch from a
                user-controllable caller would let a single request
                monopolise scoring / window / trend work.
        """
        if not agent_ids:
            return ()
        if len(agent_ids) > MAX_BATCH_SNAPSHOTS_LOOKUP:
            msg = (
                f"get_snapshots batch of {len(agent_ids)} exceeds "
                f"MAX_BATCH_SNAPSHOTS_LOOKUP={MAX_BATCH_SNAPSHOTS_LOOKUP}"
            )
            raise ValueError(msg)
        results: list[AgentPerformanceSnapshot | None] = []
        for agent_id in agent_ids:
            try:
                snapshot = await self.get_snapshot(agent_id, now=now)
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                logger.warning(
                    PERF_SNAPSHOT_FAILED,
                    agent_id=str(agent_id),
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                )
                results.append(None)
            else:
                results.append(snapshot)
        return tuple(results)

    async def get_snapshot(
        self,
        agent_id: NotBlankStr,
        *,
        now: datetime | None = None,
        since: datetime | None = None,
    ) -> AgentPerformanceSnapshot:
        """Compute a full performance snapshot for an agent.

        Args:
            agent_id: Agent to evaluate.
            now: Reference time (defaults to current UTC time).
            since: Optional lower bound on task ``completed_at``. When set,
                records before ``since`` are excluded before the rolling
                windows and the overall quality average are computed, so a
                caller observing ``[since, now]`` sees performance for that
                window rather than the agent's all-time history.

        Returns:
            Complete performance snapshot with windows and trends.
        """
        if now is None:
            now = datetime.now(UTC)

        agent_key = str(agent_id)
        async with self._metrics_lock:
            task_records = tuple(self._task_metrics.get(agent_key, []))
            # Capture the forget epoch under the SAME lock as the records
            # snapshot. If ``forget_agent`` runs after this point, the
            # captured epoch is stale relative to the bumped counter, so the
            # scheduled inflection task's guard skips its cache writes rather
            # than repopulating the forgotten agent from pre-forget trends.
            forget_epoch = self._forget_generation.get(agent_key, 0)
        if since is not None:
            # Clamp both ends so a ``[since, now]`` snapshot cannot include
            # records completed after ``now``.
            task_records = tuple(
                r for r in task_records if since <= r.completed_at <= now
            )

        # Compute windows.
        windows = self._window_strategy.compute_windows(
            task_records,
            now=now,
        )

        # Compute trends for quality and cost metrics.
        trends = self._compute_trends(task_records, windows, now=now)

        # Emit inflection events for trend direction changes. Only on
        # all-time reads: a windowed (``since``-bounded) snapshot is a
        # historical query and must not mutate the live trend-direction
        # cache or emit false inflections derived from partial history.
        if since is None and self._inflection_sink is not None and trends:
            # Schedule inside the lock so a concurrent aclear() cannot
            # snapshot the tasks, cancel, and return before this task
            # is added to ``_background_tasks`` -- otherwise the new
            # task would survive the clear and could repopulate
            # ``_trend_direction_cache`` after aclear() returned.
            async with self._metrics_lock:
                # Skip if the agent was forgotten between the records
                # snapshot and now; scheduling with the captured epoch keeps
                # the task's guard authoritative against a later forget too.
                if self._forget_generation.get(agent_key, 0) == forget_epoch:
                    self._schedule_inflection_emission(
                        agent_id, trends, forget_epoch=forget_epoch
                    )

        # Overall quality: average of the completion-oracle verdicts the
        # observer stamped onto each record. ``None`` when no reviewed task
        # is in range, so a consumer reads "not measured" rather than a zero
        # it would treat as a bad score.
        scored = [r.quality_score for r in task_records if r.quality_score is not None]
        overall_quality = round(sum(scored) / len(scored), 4) if scored else None

        snapshot = AgentPerformanceSnapshot(
            agent_id=agent_id,
            computed_at=now,
            windows=windows,
            trends=tuple(trends),
            overall_quality_score=overall_quality,
        )

        logger.info(
            PERF_SNAPSHOT_COMPUTED,
            agent_id=agent_id,
            window_count=len(windows),
            trend_count=len(trends),
        )
        return snapshot

    def _compute_trends(
        self,
        records: tuple[TaskMetricRecord, ...],
        windows: tuple[WindowMetrics, ...],
        *,
        now: datetime,
    ) -> list[TrendResult]:
        """Compute trends for key metrics across windows.

        Records are filtered to each window's time boundary so that
        e.g. the "7d" trend only considers the last 7 days of data.

        Returns:
            List of ``TrendResult``.
        """
        trends: list[TrendResult] = []
        for window in windows:
            if window.data_point_count < self._config.min_data_points:
                continue
            window_records = self._filter_records_to_window(records, window, now)
            if window_records is None:
                continue
            trends.extend(self._detect_metric_trends(window_records, window))
        return trends

    def _filter_records_to_window(
        self,
        records: tuple[TaskMetricRecord, ...],
        window: WindowMetrics,
        now: datetime,
    ) -> tuple[TaskMetricRecord, ...] | None:
        """Filter records to a window's time boundary.

        Returns None if the window label is unparseable.

        Returns:
            The resulting ``tuple[TaskMetricRecord, ...]``, or ``None`` when
            unavailable.
        """
        window_label = str(window.window_size)
        match = re.match(r"^(\d+)d$", window_label)
        if not match:
            logger.warning(
                PERF_WINDOW_INSUFFICIENT_DATA,
                window=window_label,
                warning="unparseable_window_label",
            )
            return None
        days = int(match.group(1))
        cutoff = now - timedelta(days=days)
        return tuple(r for r in records if r.completed_at >= cutoff)

    def _detect_metric_trends(
        self,
        window_records: tuple[TaskMetricRecord, ...],
        window: WindowMetrics,
    ) -> list[TrendResult]:
        """Detect quality and cost trends for window records.

        Returns:
            List of ``TrendResult``.
        """
        trends: list[TrendResult] = []
        quality_values = tuple(
            (r.completed_at, r.quality_score)
            for r in window_records
            if r.quality_score is not None
        )
        if quality_values:
            trends.append(
                self._trend_strategy.detect(
                    metric_name=NotBlankStr("quality_score"),
                    values=quality_values,
                    window_size=window.window_size,
                )
            )
        cost_values = tuple(
            (r.completed_at, r.cost) for r in window_records if r.cost is not None
        )
        if cost_values:
            trends.append(
                self._trend_strategy.detect(
                    metric_name=NotBlankStr("cost"),
                    values=cost_values,
                    window_size=window.window_size,
                )
            )
        return trends

    def get_task_metrics(
        self,
        *,
        agent_id: NotBlankStr | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> tuple[TaskMetricRecord, ...]:
        """Query raw task metric records with optional filters.

        Args:
            agent_id: Filter by agent.
            since: Include records after this time.
            until: Include records before this time.

        Returns:
            Matching task metric records.
        """
        if agent_id is not None:
            records = list(self._task_metrics.get(str(agent_id), []))
        else:
            # Snapshot ``.values()`` first: this is a sync method that
            # cannot hold the async ``_metrics_lock``, so a concurrent
            # ``record_task_metric``/``aclear`` mutating the dict mid-scan
            # would otherwise raise "dictionary changed size during
            # iteration".
            records = [r for recs in list(self._task_metrics.values()) for r in recs]

        if since is not None:
            records = [r for r in records if r.completed_at >= since]
        if until is not None:
            records = [r for r in records if r.completed_at < until]
        return tuple(records)

    @property
    def inflection_sink(self) -> InflectionSink | None:
        """Return the inflection sink, if configured."""
        return self._inflection_sink

    @inflection_sink.setter
    def inflection_sink(self, value: InflectionSink | None) -> None:
        """Set the inflection sink (startup-phase sync path).

        Not concurrency-safe: two concurrent setters both observing
        ``None`` will both succeed, silently overwriting. Use this
        setter only during single-writer startup wiring (e.g.
        :func:`synthorg.engine.evolution.factory.build_evolution_service`).
        For runtime binding from async contexts, call
        :meth:`set_inflection_sink` instead.

        Args:
            value: The inflection sink to assign.

        Raises:
            ValueError: If an inflection sink is already configured.
        """
        if self._inflection_sink is not None and value is not None:
            logger.warning(
                PERF_INFLECTION_SINK_BIND_REJECTED,
                reason="already_configured",
                path="sync_setter",
            )
            msg = "Inflection sink is already configured"
            raise ValueError(msg)
        self._inflection_sink = value
        if value is None:
            logger.info(PERF_INFLECTION_SINK_CLEARED, path="sync_setter")
        else:
            logger.info(PERF_INFLECTION_SINK_BOUND, path="sync_setter")

    async def set_inflection_sink(self, value: InflectionSink | None) -> None:
        """Atomically set the inflection sink under ``_metrics_lock``.

        The async counterpart to the sync :attr:`inflection_sink`
        setter. Two concurrent callers will be serialized; exactly one
        succeeds, the loser raises ``ValueError``. Use this from any
        async context where concurrent binding is possible (task
        engine observers, rolling evolution triggers, etc.).

        Args:
            value: The inflection sink to assign.

        Raises:
            ValueError: If an inflection sink is already configured.
        """
        async with self._metrics_lock:
            if self._inflection_sink is not None and value is not None:
                logger.warning(
                    PERF_INFLECTION_SINK_BIND_REJECTED,
                    reason="already_configured",
                    path="async_setter",
                )
                msg = "Inflection sink is already configured"
                raise ValueError(msg)
            self._inflection_sink = value
            if value is None:
                logger.info(PERF_INFLECTION_SINK_CLEARED, path="async_setter")
            else:
                logger.info(PERF_INFLECTION_SINK_BOUND, path="async_setter")

    # ── Inflection emission ─────────────────────────────────────

    def _schedule_inflection_emission(
        self,
        agent_id: NotBlankStr,
        trends: list[TrendResult],
        *,
        forget_epoch: int,
    ) -> None:
        """Schedule inflection emission as a background task.

        Compares each trend's direction against the cached previous
        direction.  Emits a ``PerformanceInflection`` for every
        direction change.  The task is tracked to prevent GC warnings.

        ``forget_epoch`` is captured by the caller under the same lock as
        the metrics snapshot the ``trends`` derive from, and re-checked in
        :meth:`_do_emit_inflections` before any cache write, so a
        ``forget_agent`` that races the snapshot cannot be repopulated.

        MUST be called with ``_metrics_lock`` held so the
        ``_background_tasks`` mutation is atomic with respect to
        :meth:`aclear` and :meth:`aclose`; otherwise a task scheduled
        here could survive a concurrent clear/close and repopulate
        ``_trend_direction_cache`` after the clear returned.
        """
        if self._closing:
            return
        task = asyncio.create_task(
            self._do_emit_inflections(agent_id, trends, forget_epoch),
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _do_emit_inflections(
        self,
        agent_id: NotBlankStr,
        trends: list[TrendResult],
        forget_epoch: int,
    ) -> None:
        """Emit inflection events for trend direction changes.

        Best-effort: failures are logged and never propagated.

        Raises:
            CancelledError: If the related operation fails.
        """
        from synthorg.hr.performance.inflection_protocol import (  # noqa: PLC0415
            PerformanceInflection,
        )

        sink = self._inflection_sink
        if sink is None:  # pragma: no cover -- guarded by caller
            return

        try:
            # Read old directions and update the cache for the WHOLE batch
            # under a single lock hold. A per-trend lock lets two concurrent
            # emitters interleave -- one reads a direction the other has
            # already overwritten -- and emit inflections that are artefacts
            # of the interleaving rather than real direction changes.
            pending: list[PerformanceInflection] = []
            async with self._metrics_lock:
                if self._forget_generation.get(str(agent_id), 0) != forget_epoch:
                    # The agent was forgotten after this task was scheduled;
                    # writing cache keys now would resurrect trend state for
                    # an agent whose footprint was just reclaimed.
                    return
                for trend in trends:
                    cache_key = (
                        str(agent_id),
                        str(trend.metric_name),
                        str(trend.window_size),
                    )
                    old_direction = self._trend_direction_cache.get(cache_key)
                    self._trend_direction_cache[cache_key] = trend.direction
                    if old_direction is not None and old_direction != trend.direction:
                        pending.append(
                            PerformanceInflection(
                                agent_id=agent_id,
                                metric_name=trend.metric_name,
                                window_size=trend.window_size,
                                old_direction=old_direction,
                                new_direction=trend.direction,
                                slope=trend.slope,
                            )
                        )
            # Emit outside the lock to allow concurrent inflections.
            for inflection in pending:
                logger.info(
                    PERF_INFLECTION_DETECTED,
                    agent_id=str(agent_id),
                    metric=str(inflection.metric_name),
                    window=str(inflection.window_size),
                    old=inflection.old_direction.value,
                    new=inflection.new_direction.value,
                )
                await sink.emit(inflection)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                PERF_INFLECTION_EMISSION_FAILED,
                agent_id=str(agent_id),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
