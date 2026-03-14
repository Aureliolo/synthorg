"""Performance tracker service.

Central service for recording and querying agent performance metrics.
Delegates scoring, windowing, and trend detection to pluggable strategies.
"""

import re
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from synthorg.core.types import NotBlankStr
from synthorg.hr.performance.config import PerformanceConfig
from synthorg.hr.performance.models import (
    AgentPerformanceSnapshot,
    CollaborationMetricRecord,
    CollaborationScoreResult,
    TaskMetricRecord,
    TrendResult,
    WindowMetrics,
)
from synthorg.observability import get_logger
from synthorg.observability.events.performance import (
    PERF_METRIC_RECORDED,
    PERF_SNAPSHOT_COMPUTED,
    PERF_WINDOW_INSUFFICIENT_DATA,
)

if TYPE_CHECKING:
    from pydantic import AwareDatetime

    from synthorg.core.task import AcceptanceCriterion
    from synthorg.hr.performance.collaboration_protocol import (
        CollaborationScoringStrategy,
    )
    from synthorg.hr.performance.quality_protocol import QualityScoringStrategy
    from synthorg.hr.performance.trend_protocol import TrendDetectionStrategy
    from synthorg.hr.performance.window_protocol import MetricsWindowStrategy

logger = get_logger(__name__)


class PerformanceTracker:
    """Central service for recording and querying agent performance metrics.

    In-memory storage keyed by agent_id. Delegates scoring, windowing,
    and trend detection to injected strategy implementations.

    When strategies are not provided, sensible defaults are constructed
    using values from the ``PerformanceConfig``.

    Args:
        quality_strategy: Strategy for scoring task quality.
        collaboration_strategy: Strategy for scoring collaboration.
        window_strategy: Strategy for computing rolling windows.
        trend_strategy: Strategy for detecting trends.
        config: Performance tracking configuration.
    """

    def __init__(
        self,
        *,
        quality_strategy: QualityScoringStrategy | None = None,
        collaboration_strategy: CollaborationScoringStrategy | None = None,
        window_strategy: MetricsWindowStrategy | None = None,
        trend_strategy: TrendDetectionStrategy | None = None,
        config: PerformanceConfig | None = None,
    ) -> None:
        cfg = config or PerformanceConfig()
        self._config = cfg
        self._quality_strategy = quality_strategy or self._default_quality()
        self._collaboration_strategy = (
            collaboration_strategy or self._default_collaboration(cfg)
        )
        self._window_strategy = window_strategy or self._default_window(cfg)
        self._trend_strategy = trend_strategy or self._default_trend(cfg)
        self._task_metrics: dict[str, list[TaskMetricRecord]] = {}
        self._collab_metrics: dict[str, list[CollaborationMetricRecord]] = {}

    @staticmethod
    def _default_quality() -> QualityScoringStrategy:
        from synthorg.hr.performance.ci_quality_strategy import (  # noqa: PLC0415
            CISignalQualityStrategy,
        )

        return CISignalQualityStrategy()

    @staticmethod
    def _default_collaboration(
        cfg: PerformanceConfig,  # noqa: ARG004
    ) -> CollaborationScoringStrategy:
        from synthorg.hr.performance.behavioral_collaboration_strategy import (  # noqa: PLC0415
            BehavioralTelemetryStrategy,
        )

        return BehavioralTelemetryStrategy()

    @staticmethod
    def _default_window(cfg: PerformanceConfig) -> MetricsWindowStrategy:
        from synthorg.hr.performance.multi_window_strategy import (  # noqa: PLC0415
            MultiWindowStrategy,
        )

        return MultiWindowStrategy(
            windows=tuple(str(w) for w in cfg.windows),
            min_data_points=cfg.min_data_points,
        )

    @staticmethod
    def _default_trend(cfg: PerformanceConfig) -> TrendDetectionStrategy:
        from synthorg.hr.performance.theil_sen_strategy import (  # noqa: PLC0415
            TheilSenTrendStrategy,
        )

        return TheilSenTrendStrategy(
            min_data_points=cfg.min_data_points,
            improving_threshold=cfg.improving_threshold,
            declining_threshold=cfg.declining_threshold,
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
        return record

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

        Args:
            record: Collaboration metric record to store.
        """
        agent_key = str(record.agent_id)
        if agent_key not in self._collab_metrics:
            self._collab_metrics[agent_key] = []
        self._collab_metrics[agent_key].append(record)

        logger.debug(
            PERF_METRIC_RECORDED,
            agent_id=record.agent_id,
            metric_type="collaboration",
        )

    async def get_collaboration_score(
        self,
        agent_id: NotBlankStr,
    ) -> CollaborationScoreResult:
        """Compute collaboration score for an agent.

        Args:
            agent_id: Agent to evaluate.

        Returns:
            Collaboration score result.
        """
        records = tuple(self._collab_metrics.get(str(agent_id), []))
        return await self._collaboration_strategy.score(
            agent_id=agent_id,
            records=records,
        )

    async def get_snapshot(
        self,
        agent_id: NotBlankStr,
        *,
        now: AwareDatetime | None = None,
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
        collab_records = tuple(self._collab_metrics.get(agent_key, []))

        # Compute windows.
        windows = self._window_strategy.compute_windows(
            task_records,
            now=now,
        )

        # Compute trends for quality and cost metrics.
        trends = self._compute_trends(task_records, windows, now=now)

        # Overall quality: average of all scored records.
        scored = [r.quality_score for r in task_records if r.quality_score is not None]
        overall_quality = round(sum(scored) / len(scored), 4) if scored else None

        # Overall collaboration score.
        collab_result = await self._collaboration_strategy.score(
            agent_id=agent_id,
            records=collab_records,
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
        now: AwareDatetime,
    ) -> list[TrendResult]:
        """Compute trends for key metrics across windows.

        Records are filtered to each window's time boundary so that
        e.g. the "7d" trend only considers the last 7 days of data.
        """
        trends: list[TrendResult] = []
        for window in windows:
            if window.data_point_count < self._config.min_data_points:
                continue

            # Filter records to this window's time boundary.
            window_label = str(window.window_size)
            match = re.match(r"^(\d+)d$", window_label)
            if match:
                days = int(match.group(1))
                cutoff = now - timedelta(days=days)
                window_records = tuple(r for r in records if r.completed_at >= cutoff)
            else:
                logger.warning(
                    PERF_WINDOW_INSUFFICIENT_DATA,
                    window=window_label,
                    warning="unparseable_window_label",
                )
                window_records = records

            # Quality score trend.
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
            # Cost trend.
            cost_values = tuple((r.completed_at, r.cost_usd) for r in window_records)
            if cost_values:
                trends.append(
                    self._trend_strategy.detect(
                        metric_name=NotBlankStr("cost_usd"),
                        values=cost_values,
                        window_size=window.window_size,
                    )
                )
        return trends

    def get_task_metrics(
        self,
        *,
        agent_id: NotBlankStr | None = None,
        since: AwareDatetime | None = None,
        until: AwareDatetime | None = None,
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
            records = [r for r in records if r.completed_at <= until]
        return tuple(records)

    def get_collaboration_metrics(
        self,
        *,
        agent_id: NotBlankStr | None = None,
        since: AwareDatetime | None = None,
        until: AwareDatetime | None = None,
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
            records = [r for r in records if r.recorded_at <= until]
        return tuple(records)
