"""Performance tracker service.

Central service for recording and querying agent performance metrics.
Delegates scoring, windowing, and trend detection to pluggable strategies.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from ai_company.core.types import NotBlankStr
from ai_company.hr.performance.config import PerformanceConfig
from ai_company.hr.performance.models import (
    AgentPerformanceSnapshot,
    CollaborationMetricRecord,
    CollaborationScoreResult,
    TaskMetricRecord,
    TrendResult,
    WindowMetrics,
)
from ai_company.observability import get_logger
from ai_company.observability.events.performance import (
    PERF_METRIC_RECORDED,
    PERF_SNAPSHOT_COMPUTED,
)

if TYPE_CHECKING:
    from pydantic import AwareDatetime

    from ai_company.core.task import AcceptanceCriterion
    from ai_company.hr.performance.collaboration_protocol import (
        CollaborationScoringStrategy,
    )
    from ai_company.hr.performance.quality_protocol import QualityScoringStrategy
    from ai_company.hr.performance.trend_protocol import TrendDetectionStrategy
    from ai_company.hr.performance.window_protocol import MetricsWindowStrategy

logger = get_logger(__name__)


class PerformanceTracker:
    """Central service for recording and querying agent performance metrics.

    In-memory storage keyed by agent_id. Delegates scoring, windowing,
    and trend detection to injected strategy implementations.

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
        quality_strategy: QualityScoringStrategy,
        collaboration_strategy: CollaborationScoringStrategy,
        window_strategy: MetricsWindowStrategy,
        trend_strategy: TrendDetectionStrategy,
        config: PerformanceConfig | None = None,
    ) -> None:
        self._quality_strategy = quality_strategy
        self._collaboration_strategy = collaboration_strategy
        self._window_strategy = window_strategy
        self._trend_strategy = trend_strategy
        self._config = config or PerformanceConfig()
        self._task_metrics: dict[str, list[TaskMetricRecord]] = {}
        self._collab_metrics: dict[str, list[CollaborationMetricRecord]] = {}

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
        trends = self._compute_trends(task_records, windows)

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
    ) -> list[TrendResult]:
        """Compute trends for key metrics across windows."""
        trends: list[TrendResult] = []
        for window in windows:
            if window.data_point_count < self._config.min_data_points:
                continue
            # Quality score trend.
            quality_values = tuple(
                (r.completed_at, r.quality_score)
                for r in records
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
            cost_values = tuple((r.completed_at, r.cost_usd) for r in records)
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
    ) -> tuple[CollaborationMetricRecord, ...]:
        """Query collaboration metric records with optional filters.

        Args:
            agent_id: Filter by agent.
            since: Include records after this time.

        Returns:
            Matching collaboration metric records.
        """
        if agent_id is not None:
            records = list(self._collab_metrics.get(str(agent_id), []))
        else:
            records = [r for recs in self._collab_metrics.values() for r in recs]

        if since is not None:
            records = [r for r in records if r.recorded_at >= since]
        return tuple(records)
