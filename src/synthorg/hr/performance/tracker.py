# module-kind: complex_service
"""Performance tracker service.

Central service for recording and querying agent performance metrics.
Delegates scoring, windowing, and trend detection to pluggable
strategies.

One cohesive responsibility: track per-agent performance. Metric
recording, snapshot computation (windows + trends), LLM-sampler
scheduling, and inflection emission all read and mutate the same
state under ``_metrics_lock`` (the ``_background_tasks`` set, the
``_closing`` flag, the ``_trend_direction_cache``); they form one
single-threaded pipeline that owns those invariants. A per-concern
split would either duplicate the lock + flags across modules or
introduce a separate coordinator that re-establishes the same
boundary at a higher cost. The strategy seams (quality /
collaboration / window / trend / inflection sink) already isolate
the pluggable algorithms.
"""

import asyncio
import math
import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.task import AcceptanceCriterion
from synthorg.core.types import NotBlankStr
from synthorg.engine.coordination.attribution import AgentContribution
from synthorg.hr.enums import TrendDirection
from synthorg.hr.performance.collaboration_override_store import (
    CollaborationOverrideStore,
)
from synthorg.hr.performance.collaboration_protocol import (
    CollaborationScoringStrategy,
)
from synthorg.hr.performance.config import PerformanceConfig
from synthorg.hr.performance.inflection_protocol import InflectionSink
from synthorg.hr.performance.llm_calibration_sampler import (
    LlmCalibrationSampler,
)
from synthorg.hr.performance.models import (
    AgentPerformanceSnapshot,
    CollaborationCalibration,
    CollaborationMetricRecord,
    CollaborationOverride,
    CollaborationScoreResult,
    TaskMetricRecord,
    TrendResult,
    WindowMetrics,
)
from synthorg.hr.performance.quality_override_store import (
    QualityOverrideStore,
)
from synthorg.hr.performance.quality_protocol import QualityScoringStrategy
from synthorg.hr.performance.trend_protocol import TrendDetectionStrategy
from synthorg.hr.performance.window_protocol import MetricsWindowStrategy
from synthorg.hr.persistence_protocol import (
    CollaborationMetricRepository,
    TaskMetricRepository,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.inflection import (
    PERF_INFLECTION_DETECTED,
    PERF_INFLECTION_EMISSION_FAILED,
)
from synthorg.observability.events.performance import (
    PERF_BACKGROUND_TASK_FAILED,
    PERF_INFLECTION_SINK_BIND_REJECTED,
    PERF_INFLECTION_SINK_BOUND,
    PERF_INFLECTION_SINK_CLEARED,
    PERF_LLM_SAMPLE_FAILED,
    PERF_METRIC_PERSIST_FAILED,
    PERF_METRIC_RECORDED,
    PERF_OVERRIDE_APPLIED,
    PERF_SNAPSHOT_COMPUTED,
    PERF_SNAPSHOT_FAILED,
    PERF_TRACKER_CLEARED,
    PERF_WINDOW_INSUFFICIENT_DATA,
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


def _coerce_finite_weights(
    raw: Iterable[tuple[str, float]],
) -> tuple[tuple[NotBlankStr, float], ...]:
    """Coerce ``describe_weights()`` output into a calibration-safe tuple.

    ``CollaborationCalibration.component_weights`` runs under
    ``allow_inf_nan=False``; a strategy returning a ``NaN`` or ``Inf``
    weight would surface as a Pydantic ``ValidationError`` at model
    construction and take down the whole calibration readout. Reject
    non-finite values here with a plain ``ValueError`` so the
    surrounding ``except Exception`` path (which logs and falls back
    to ``component_weights=()``) catches the failure cleanly.

    Returns:
        Tuple of ``tuple[NotBlankStr, float]``.

    Raises:
        ValueError: If an argument fails domain validation.
    """
    coerced: list[tuple[NotBlankStr, float]] = []
    for name, value in raw:
        weight = float(value)
        if not math.isfinite(weight):
            msg = f"Non-finite weight for component {name!r}: {value!r}"
            raise ValueError(msg)
        coerced.append((NotBlankStr(name), weight))
    return tuple(coerced)


class PerformanceTracker:
    """Central service for recording and querying agent performance metrics.

    In-memory storage keyed by agent_id. Delegates scoring, windowing,
    and trend detection to injected strategy implementations.

    When strategies are not provided, sensible defaults are constructed
    (window and trend strategies use values from ``PerformanceConfig``).

    Args:
        quality_strategy: Strategy for scoring task quality.
        collaboration_strategy: Strategy for scoring collaboration.
        window_strategy: Strategy for computing rolling windows.
        trend_strategy: Strategy for detecting trends.
        config: Performance tracking configuration.
        sampler: LLM calibration sampler (None = disabled).
        override_store: Collaboration override store (None = disabled).
        quality_override_store: Quality override store (None = disabled).
    """

    def __init__(  # noqa: PLR0913
        self,
        *,
        quality_strategy: QualityScoringStrategy | None = None,
        collaboration_strategy: CollaborationScoringStrategy | None = None,
        window_strategy: MetricsWindowStrategy | None = None,
        trend_strategy: TrendDetectionStrategy | None = None,
        config: PerformanceConfig | None = None,
        sampler: LlmCalibrationSampler | None = None,
        override_store: CollaborationOverrideStore | None = None,
        quality_override_store: QualityOverrideStore | None = None,
        inflection_sink: InflectionSink | None = None,
        task_metric_repo: TaskMetricRepository | None = None,
        collab_metric_repo: CollaborationMetricRepository | None = None,
    ) -> None:
        cfg = config or PerformanceConfig()
        self._config = cfg
        self._task_metric_repo = task_metric_repo
        self._collab_metric_repo = collab_metric_repo
        self._quality_strategy = quality_strategy or self._default_quality()
        self._collaboration_strategy = (
            collaboration_strategy or self._default_collaboration(cfg)
        )
        self._window_strategy = window_strategy or self._default_window(cfg)
        self._trend_strategy = trend_strategy or self._default_trend(cfg)
        self._sampler = sampler
        self._override_store = override_store
        self._quality_override_store = quality_override_store
        self._inflection_sink = inflection_sink
        self._trend_direction_cache: dict[tuple[str, str, str], TrendDirection] = {}
        self._task_metrics: dict[str, list[TaskMetricRecord]] = {}
        self._collab_metrics: dict[str, list[CollaborationMetricRecord]] = {}
        self._contributions: dict[str, list[AgentContribution]] = {}
        self._background_tasks: set[asyncio.Task[None]] = set()
        self._metrics_lock = asyncio.Lock()
        # Set to True while ``aclose()`` is draining so new background
        # tasks cannot be enqueued between the task-set snapshot and
        # the clear. Guarded by ``_metrics_lock`` on both read and
        # write sides.
        self._closing: bool = False

    @staticmethod
    def _default_quality() -> QualityScoringStrategy:
        """Default quality.

        Returns:
            Result of type ``QualityScoringStrategy``.
        """
        from synthorg.hr.performance.ci_quality_strategy import (  # noqa: PLC0415
            CISignalQualityStrategy,
        )

        return CISignalQualityStrategy()

    @staticmethod
    def _default_collaboration(
        cfg: PerformanceConfig,  # noqa: ARG004
    ) -> CollaborationScoringStrategy:
        """Default collaboration.

        Returns:
            Result of type ``CollaborationScoringStrategy``.
        """
        from synthorg.hr.performance.behavioral_collaboration_strategy import (  # noqa: PLC0415
            BehavioralTelemetryStrategy,
        )

        return BehavioralTelemetryStrategy()

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

    def clear(self) -> None:
        """Synchronously reset all recorded metrics for test isolation.

        Cancels pending background tasks via ``Task.cancel()`` but does
        **not** await them. This is the test-only sync companion to
        :meth:`aclear`, mirroring the uniform ``clear()`` sync-reset
        surface the other trackers expose to the sync test-reset fixture
        (where no running event loop is available). Production code uses
        the async :meth:`aclear` / :meth:`aclose` instead.
        """
        tasks_cancelled = len(self._background_tasks)
        task_metrics_cleared = len(self._task_metrics)
        collab_metrics_cleared = len(self._collab_metrics)
        contributions_cleared = len(self._contributions)
        trend_cache_cleared = len(self._trend_direction_cache)
        # Iterate over a snapshot: task done-callbacks remove from the
        # set, so iterating the live set would raise
        # ``RuntimeError: set changed size during iteration``.
        for t in list(self._background_tasks):
            t.cancel()
        self._background_tasks.clear()
        self._task_metrics.clear()
        self._collab_metrics.clear()
        self._contributions.clear()
        self._trend_direction_cache.clear()
        logger.info(
            PERF_TRACKER_CLEARED,
            tasks_cancelled=tasks_cancelled,
            task_metrics_cleared=task_metrics_cleared,
            collab_metrics_cleared=collab_metrics_cleared,
            contributions_cleared=contributions_cleared,
            trend_cache_cleared=trend_cache_cleared,
        )

    async def aclose(self) -> None:
        """Cancel and await all pending background tasks.

        Should be called during application shutdown to prevent
        ``RuntimeError: Task was destroyed but it is pending!``
        warnings.

        Sets ``_closing`` under ``_metrics_lock`` before snapshotting
        so concurrent ``record_collaboration_event`` / ``get_snapshot``
        calls (which schedule under the same lock) refuse to enqueue
        new background tasks once shutdown has started. Without that
        gate a task scheduled right after the snapshot would survive
        aclose() with the result that the caller sees
        ``aclose() returned`` while a live sampling / inflection task
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
        / ``_collab_metrics`` / ``_contributions`` /
        ``_trend_direction_cache``. Cancels pending background tasks
        *without* awaiting them (matches :meth:`clear` semantics) so
        the call is cheap in hot tests.

        Production callers that must drain outstanding tasks cleanly
        should call :meth:`aclose` instead.
        """
        async with self._metrics_lock:
            tasks_cancelled = len(self._background_tasks)
            task_metrics_cleared = len(self._task_metrics)
            collab_metrics_cleared = len(self._collab_metrics)
            contributions_cleared = len(self._contributions)
            trend_cache_cleared = len(self._trend_direction_cache)
            for t in list(self._background_tasks):
                t.cancel()
            self._background_tasks.clear()
            self._task_metrics.clear()
            self._collab_metrics.clear()
            self._contributions.clear()
            self._trend_direction_cache.clear()
        logger.info(
            PERF_TRACKER_CLEARED,
            tasks_cancelled=tasks_cancelled,
            task_metrics_cleared=task_metrics_cleared,
            collab_metrics_cleared=collab_metrics_cleared,
            contributions_cleared=contributions_cleared,
            trend_cache_cleared=trend_cache_cleared,
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
        collab_metric_repo: CollaborationMetricRepository,
    ) -> None:
        """Attach the durable metric repos post-connect.

        The tracker is built at the synchronous construction phase before
        persistence is connected, so the durable repos are wired here once
        a backend exists. Called once at boot before traffic, so the plain
        attribute assignment is safe without a lock.
        """
        self._task_metric_repo = task_metric_repo
        self._collab_metric_repo = collab_metric_repo

    async def _persist_metric(
        self,
        repo: TaskMetricRepository | CollaborationMetricRepository | None,
        record: TaskMetricRecord | CollaborationMetricRecord,
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
                await repo.save(record)  # type: ignore[arg-type]
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                PERF_METRIC_PERSIST_FAILED,
                agent_id=agent_id,
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

    async def score_task_quality(
        self,
        *,
        agent_id: NotBlankStr,
        task_id: NotBlankStr,
        task_result: TaskMetricRecord,
        acceptance_criteria: tuple[AcceptanceCriterion, ...] = (),
    ) -> TaskMetricRecord:
        """Score task quality and update the record.

        Args:
            agent_id: Agent who completed the task.
            task_id: Task identifier.
            task_result: Recorded task metrics.
            acceptance_criteria: Criteria to evaluate against.

        Returns:
            Updated record with quality score.
        """
        result = await self._quality_strategy.score(
            agent_id=agent_id,
            task_id=task_id,
            task_result=task_result,
            acceptance_criteria=acceptance_criteria,
        )
        return task_result.model_copy(update={"quality_score": result.score})

    async def record_collaboration_event(
        self,
        record: CollaborationMetricRecord,
    ) -> None:
        """Record a collaboration behavior data point.

        If an LLM sampler is configured and the record has an
        ``interaction_summary``, the sampler is invoked probabilistically.

        Args:
            record: Collaboration metric record to store.
        """
        agent_key = str(record.agent_id)
        async with self._metrics_lock:
            if agent_key not in self._collab_metrics:
                self._collab_metrics[agent_key] = []
            self._collab_metrics[agent_key].append(record)
            # Schedule inside the lock so a concurrent aclear() cannot
            # snapshot the tasks, cancel, and return before this task
            # is added to ``_background_tasks`` -- otherwise the new
            # task would survive the clear and could repopulate cache
            # state after aclear() returned.
            self._schedule_sampling(record)

        logger.debug(
            PERF_METRIC_RECORDED,
            agent_id=record.agent_id,
            metric_type="collaboration",
        )
        await self._persist_metric(
            self._collab_metric_repo, record, agent_id=str(record.agent_id)
        )

    async def get_collaboration_score(
        self,
        agent_id: NotBlankStr,
        *,
        now: datetime | None = None,
    ) -> CollaborationScoreResult:
        """Compute collaboration score for an agent.

        Returns the active human override if one exists; otherwise
        delegates to the collaboration scoring strategy.

        Args:
            agent_id: Agent to evaluate.
            now: Reference time for override expiration check
                (defaults to current UTC time).

        Returns:
            Collaboration score result.
        """
        if self._override_store is not None:
            override = self._override_store.get_active_override(
                agent_id,
                now=now,
            )
            if override is not None:
                logger.info(
                    PERF_OVERRIDE_APPLIED,
                    agent_id=agent_id,
                    score=override.score,
                    applied_by=override.applied_by,
                )
                return CollaborationScoreResult(
                    score=override.score,
                    strategy_name=NotBlankStr("human_override"),
                    component_scores=(),
                    confidence=1.0,
                    override_active=True,
                )

        # Snapshot under the lock so a future refactor that introduces
        # an ``await`` between the dict read and the tuple copy cannot
        # tear the records list. Strategy scoring runs *outside* the
        # lock -- it may do unbounded work and must not serialize
        # concurrent record writes.
        async with self._metrics_lock:
            records = tuple(self._collab_metrics.get(str(agent_id), []))
        return await self._collaboration_strategy.score(
            agent_id=agent_id,
            records=records,
        )

    async def get_collaboration_calibration(
        self,
        agent_id: NotBlankStr,
    ) -> CollaborationCalibration:
        """Return a stable calibration readout for an agent.

        The shape is deliberately curated -- ``strategy_name`` and the
        bounded ``component_weights`` map describe the active scoring
        strategy without leaking strategy-private internals. Swapping
        the underlying strategy never changes the envelope shape.

        Args:
            agent_id: Agent to read calibration for.

        Returns:
            ``CollaborationCalibration`` covering the active strategy,
            window labels, sample size, override (if any), and last
            calibration timestamp.
        """
        strategy = self._collaboration_strategy
        strategy_name = NotBlankStr(strategy.name)

        # ``describe_weights`` is optional on the protocol; empty tuple
        # is a valid response for strategies that do not advertise
        # weights (or for hand-rolled stubs in tests).
        describe = getattr(strategy, "describe_weights", None)
        weights: tuple[tuple[NotBlankStr, float], ...] = ()
        if callable(describe):
            try:
                raw = describe()
                weights = _coerce_finite_weights(raw)
            except Exception as exc:  # noqa: BLE001 -- criticals re-raised
                reraise_critical(exc)
                # Coercion failures (malformed pair, blank component name,
                # non-numeric weight) are folded into the same fail-soft
                # path as ``describe_weights`` raising directly so one bad
                # strategy descriptor cannot take down the whole calibration
                # readout. The caller surfaces ``component_weights=()`` and
                # the warning lets ops trace the source.
                logger.warning(
                    PERF_SNAPSHOT_FAILED,
                    agent_id=str(agent_id),
                    error=safe_error_description(exc),
                    error_type=type(exc).__name__,
                    where="describe_weights",
                )
                weights = ()

        active_override: CollaborationOverride | None = None
        if self._override_store is not None:
            active_override = self._override_store.get_active_override(agent_id)

        async with self._metrics_lock:
            records = tuple(self._collab_metrics.get(str(agent_id), []))
        last_calibrated_at: datetime | None = None
        if records:
            last_calibrated_at = max(r.recorded_at for r in records)

        return CollaborationCalibration(
            agent_id=agent_id,
            strategy_name=strategy_name,
            window_sizes=tuple(self._config.windows),
            component_weights=weights,
            active_override=active_override,
            sample_size=len(records),
            last_calibrated_at=last_calibrated_at,
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
    ) -> AgentPerformanceSnapshot:
        """Compute a full performance snapshot for an agent.

        Args:
            agent_id: Agent to evaluate.
            now: Reference time (defaults to current UTC time).

        Returns:
            Complete performance snapshot with windows and trends.
        """
        if now is None:
            now = datetime.now(UTC)

        agent_key = str(agent_id)
        task_records = tuple(self._task_metrics.get(agent_key, []))

        # Compute windows.
        windows = self._window_strategy.compute_windows(
            task_records,
            now=now,
        )

        # Compute trends for quality and cost metrics.
        trends = self._compute_trends(task_records, windows, now=now)

        # Emit inflection events for trend direction changes.
        if self._inflection_sink is not None and trends:
            # Schedule inside the lock so a concurrent aclear() cannot
            # snapshot the tasks, cancel, and return before this task
            # is added to ``_background_tasks`` -- otherwise the new
            # task would survive the clear and could repopulate
            # ``_trend_direction_cache`` after aclear() returned.
            async with self._metrics_lock:
                self._schedule_inflection_emission(agent_id, trends)

        # Overall quality: average of all scored records.
        scored = [r.quality_score for r in task_records if r.quality_score is not None]
        overall_quality = round(sum(scored) / len(scored), 4) if scored else None

        # Overall collaboration score (respects active overrides).
        collab_result = await self.get_collaboration_score(
            agent_id,
            now=now,
        )
        overall_collab = collab_result.score if collab_result.confidence > 0.0 else None

        snapshot = AgentPerformanceSnapshot(
            agent_id=agent_id,
            computed_at=now,
            windows=windows,
            trends=tuple(trends),
            overall_quality_score=overall_quality,
            overall_collaboration_score=overall_collab,
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
        cost_values = tuple((r.completed_at, r.cost) for r in window_records)
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
            records = [r for recs in self._task_metrics.values() for r in recs]

        if since is not None:
            records = [r for r in records if r.completed_at >= since]
        if until is not None:
            records = [r for r in records if r.completed_at < until]
        return tuple(records)

    def get_collaboration_metrics(
        self,
        *,
        agent_id: NotBlankStr | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> tuple[CollaborationMetricRecord, ...]:
        """Query collaboration metric records with optional filters.

        Args:
            agent_id: Filter by agent.
            since: Include records after this time.
            until: Include records before this time.

        Returns:
            Matching collaboration metric records.
        """
        if agent_id is not None:
            records = list(self._collab_metrics.get(str(agent_id), []))
        else:
            records = [r for recs in self._collab_metrics.values() for r in recs]

        if since is not None:
            records = [r for r in records if r.recorded_at >= since]
        if until is not None:
            records = [r for r in records if r.recorded_at < until]
        return tuple(records)

    @property
    def override_store(self) -> CollaborationOverrideStore | None:
        """Return the collaboration override store, if configured."""
        return self._override_store

    @property
    def quality_override_store(self) -> QualityOverrideStore | None:
        """Return the quality override store, if configured."""
        return self._quality_override_store

    @property
    def sampler(self) -> LlmCalibrationSampler | None:
        """Return the LLM calibration sampler, if configured."""
        return self._sampler

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

    def _schedule_sampling(
        self,
        record: CollaborationMetricRecord,
    ) -> None:
        """Schedule LLM sampling as a background task.

        The task is tracked in ``_background_tasks`` to prevent
        garbage-collection warnings. Failures are handled inside
        ``_maybe_sample`` -- they never propagate.

        MUST be called with ``_metrics_lock`` held so the
        ``_background_tasks`` mutation is atomic with respect to
        :meth:`aclear` and :meth:`aclose`; otherwise a task scheduled
        here could survive a concurrent clear/close and repopulate
        metric state (e.g. ``_trend_direction_cache``) after the
        clear returned.
        """
        # Refuse to enqueue after ``aclose()`` has started; otherwise
        # the post-aclose task would leak and keep running against a
        # tracker the caller considers shut down.
        if self._closing:
            return
        if self._sampler is None:
            return
        if record.interaction_summary is None:
            return
        if not self._sampler.should_sample():
            return

        task = asyncio.create_task(self._maybe_sample(record))
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _maybe_sample(
        self,
        record: CollaborationMetricRecord,
    ) -> None:
        """Execute LLM sampling for a single record.

        Called as a background task by ``_schedule_sampling``.
        Failures are caught and logged -- sampling must never propagate
        exceptions to the caller.
        """
        sampler = self._sampler
        if sampler is None:  # pragma: no cover -- guarded by _schedule_sampling
            return

        try:
            behavioral_result = await self._collaboration_strategy.score(
                agent_id=record.agent_id,
                records=(record,),
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                PERF_LLM_SAMPLE_FAILED,
                agent_id=record.agent_id,
                record_id=record.id,
                reason="behavioral_score_failed",
            )
            return

        try:
            await sampler.sample(
                record=record,
                behavioral_score=behavioral_result.score,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                PERF_LLM_SAMPLE_FAILED,
                agent_id=record.agent_id,
                record_id=record.id,
                reason="llm_sample_failed",
            )

    # ── Inflection emission ─────────────────────────────────────

    def _schedule_inflection_emission(
        self,
        agent_id: NotBlankStr,
        trends: list[TrendResult],
    ) -> None:
        """Schedule inflection emission as a background task.

        Compares each trend's direction against the cached previous
        direction.  Emits a ``PerformanceInflection`` for every
        direction change.  The task is tracked to prevent GC warnings.

        MUST be called with ``_metrics_lock`` held so the
        ``_background_tasks`` mutation is atomic with respect to
        :meth:`aclear` and :meth:`aclose`; otherwise a task scheduled
        here could survive a concurrent clear/close and repopulate
        ``_trend_direction_cache`` after the clear returned.
        """
        if self._closing:
            return
        task = asyncio.create_task(
            self._do_emit_inflections(agent_id, trends),
        )
        self._background_tasks.add(task)
        task.add_done_callback(self._background_tasks.discard)

    async def _do_emit_inflections(
        self,
        agent_id: NotBlankStr,
        trends: list[TrendResult],
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
            for trend in trends:
                cache_key = (
                    str(agent_id),
                    str(trend.metric_name),
                    str(trend.window_size),
                )
                # Atomically read old direction and update cache (TOCTOU fix).
                async with self._metrics_lock:
                    old_direction = self._trend_direction_cache.get(
                        cache_key,
                    )
                    self._trend_direction_cache[cache_key] = trend.direction
                # Emit outside lock to allow concurrent inflections.
                if old_direction is not None and old_direction != trend.direction:
                    inflection = PerformanceInflection(
                        agent_id=agent_id,
                        metric_name=trend.metric_name,
                        window_size=trend.window_size,
                        old_direction=old_direction,
                        new_direction=trend.direction,
                        slope=trend.slope,
                    )
                    logger.info(
                        PERF_INFLECTION_DETECTED,
                        agent_id=str(agent_id),
                        metric=str(trend.metric_name),
                        window=str(trend.window_size),
                        old=old_direction.value,
                        new=trend.direction.value,
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
