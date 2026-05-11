"""Pruning policy protocol and implementations.

Defines pluggable strategies for evaluating whether an agent should
be pruned based on performance data.
"""

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.types import NotBlankStr
from synthorg.hr.enums import TrendDirection
from synthorg.hr.pruning.models import PruningEvaluation
from synthorg.observability import get_logger

if TYPE_CHECKING:
    from synthorg.hr.performance.models import (
        AgentPerformanceSnapshot,
        WindowMetrics,
    )
from synthorg.observability.events.hr import (
    HR_PRUNING_EVALUATION_COMPLETE,
)

logger = get_logger(__name__)

_EXPECTED_WINDOWS = ("7d", "30d", "90d")


# ThresholdPruningPolicy + TrendPruningPolicy impls; cross-area consumer
# (PruningService + scaling PerformancePruningStrategy).
@runtime_checkable
class PruningPolicy(Protocol):
    """Strategy for evaluating whether an agent should be pruned."""

    async def evaluate(
        self,
        agent_id: NotBlankStr,
        snapshot: AgentPerformanceSnapshot,
    ) -> PruningEvaluation:
        """Evaluate if agent should be pruned based on performance data.

        Args:
            agent_id: The agent being evaluated.
            snapshot: Current performance snapshot.

        Returns:
            Evaluation result with eligibility and reasons.
        """
        ...


# ── Threshold Policy ─────────────────────────────────────────


class ThresholdPruningPolicyConfig(BaseModel):
    """Configuration for threshold-based pruning.

    Attributes:
        quality_threshold: Quality score floor (0-10).
        collaboration_threshold: Collaboration score floor (0-10).
        minimum_consecutive_windows: Windows that must be below threshold.
        minimum_window_data_points: Minimum records to evaluate a window.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    quality_threshold: float = Field(
        default=3.5,
        ge=0.0,
        le=10.0,
        description="Quality score floor",
    )
    collaboration_threshold: float = Field(
        default=3.5,
        ge=0.0,
        le=10.0,
        description="Collaboration score floor",
    )
    minimum_consecutive_windows: int = Field(
        default=2,
        ge=1,
        le=3,
        description="Consecutive windows below threshold required",
    )
    minimum_window_data_points: int = Field(
        default=5,
        ge=1,
        description="Minimum data points to evaluate a window",
    )


class ThresholdPruningPolicy:
    """Prune agents with quality and collaboration below thresholds.

    Agent is eligible if N+ consecutive windows (ordered by size)
    have both ``avg_quality_score`` and ``collaboration_score`` strictly
    below the configured thresholds, with sufficient data points.

    Windows with ``None`` scores or insufficient data points break
    the consecutive streak.
    """

    def __init__(self, config: ThresholdPruningPolicyConfig) -> None:
        self._config = config

    async def evaluate(
        self,
        agent_id: NotBlankStr,
        snapshot: AgentPerformanceSnapshot,
    ) -> PruningEvaluation:
        """Check quality/collaboration against thresholds.

        Args:
            agent_id: The agent being evaluated.
            snapshot: Current performance snapshot.

        Returns:
            Evaluation result with eligibility and reasons.
        """
        now = datetime.now(UTC)
        windows_by_size = {str(w.window_size): w for w in snapshot.windows}

        consecutive = 0
        max_consecutive = 0
        current_failing: list[str] = []
        best_failing: list[str] = []

        for size in _EXPECTED_WINDOWS:
            window = windows_by_size.get(size)
            if window and self._window_qualifies(window):
                consecutive += 1
                current_failing.append(size)
                if consecutive > max_consecutive:
                    max_consecutive = consecutive
                    best_failing = current_failing.copy()
            else:
                consecutive = 0
                current_failing.clear()

        eligible = max_consecutive >= self._config.minimum_consecutive_windows

        reasons: tuple[NotBlankStr, ...] = ()
        if eligible:
            windows_str = ", ".join(best_failing)
            reasons = (
                NotBlankStr(
                    f"Quality and collaboration below thresholds "
                    f"in {windows_str} windows"
                ),
            )

        scores = self._build_scores(snapshot)

        logger.info(
            HR_PRUNING_EVALUATION_COMPLETE,
            agent_id=str(agent_id),
            policy="threshold",
            eligible=eligible,
            consecutive_windows=max_consecutive,
        )

        return PruningEvaluation(
            agent_id=agent_id,
            eligible=eligible,
            reasons=reasons,
            scores=scores,
            policy_name=NotBlankStr("threshold"),
            snapshot=snapshot,
            evaluated_at=now,
        )

    def _window_qualifies(self, window: WindowMetrics) -> bool:
        """Check if a single window fails both thresholds."""
        if window.data_point_count < self._config.minimum_window_data_points:
            return False
        if window.avg_quality_score is None or window.collaboration_score is None:
            return False
        return (
            window.avg_quality_score < self._config.quality_threshold
            and window.collaboration_score < self._config.collaboration_threshold
        )

    def _build_scores(
        self,
        snapshot: AgentPerformanceSnapshot,
    ) -> dict[str, float]:
        """Build debug scores from snapshot data."""
        scores: dict[str, float] = {}
        if snapshot.overall_quality_score is not None:
            scores["overall_quality"] = snapshot.overall_quality_score
        if snapshot.overall_collaboration_score is not None:
            scores["overall_collaboration"] = snapshot.overall_collaboration_score
        for window in snapshot.windows:
            size = str(window.window_size)
            if window.avg_quality_score is not None:
                scores[f"quality_{size}"] = window.avg_quality_score
            if window.collaboration_score is not None:
                scores[f"collaboration_{size}"] = window.collaboration_score
        return scores


# ── Trend Policy ─────────────────────────────────────────────


class TrendPruningPolicyConfig(BaseModel):
    """Configuration for trend-based pruning.

    Attributes:
        minimum_data_points_per_window: Min data points per trend window.
        metric_name: Which metric to track for trend evaluation.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    minimum_data_points_per_window: int = Field(
        default=5,
        ge=1,
        description="Minimum data points per trend window",
    )
    metric_name: NotBlankStr = Field(
        default=NotBlankStr("quality_score"),
        description="Metric to track for trend evaluation",
    )


class TrendPruningPolicy:
    """Prune agents with sustained negative trends across all windows.

    Agent is eligible if all three windows (7d, 30d, 90d) show
    ``DECLINING`` direction for the configured metric, with sufficient
    data points per window.

    Trends with ``INSUFFICIENT_DATA`` direction or below minimum data
    points are treated as non-declining (agent not eligible).
    """

    def __init__(self, config: TrendPruningPolicyConfig) -> None:
        self._config = config

    async def evaluate(
        self,
        agent_id: NotBlankStr,
        snapshot: AgentPerformanceSnapshot,
    ) -> PruningEvaluation:
        """Check all windows for consistent declining trends.

        Args:
            agent_id: The agent being evaluated.
            snapshot: Current performance snapshot.

        Returns:
            Evaluation result with eligibility and reasons.
        """
        now = datetime.now(UTC)

        qualifying_trends = [
            t
            for t in snapshot.trends
            if (
                str(t.metric_name) == str(self._config.metric_name)
                and t.data_point_count >= self._config.minimum_data_points_per_window
            )
        ]

        declining_windows: dict[str, float] = {}
        for trend in qualifying_trends:
            if trend.direction == TrendDirection.DECLINING:
                declining_windows[str(trend.window_size)] = trend.slope

        eligible = all(w in declining_windows for w in _EXPECTED_WINDOWS)

        reasons: tuple[NotBlankStr, ...] = ()
        if eligible:
            reasons = (
                NotBlankStr(
                    f"Declining {self._config.metric_name} trend "
                    f"across all windows (7d, 30d, 90d)"
                ),
            )

        scores: dict[str, float] = {
            f"slope_{w}": s for w, s in declining_windows.items()
        }

        logger.info(
            HR_PRUNING_EVALUATION_COMPLETE,
            agent_id=str(agent_id),
            policy="trend",
            eligible=eligible,
            declining_windows=len(declining_windows),
        )

        return PruningEvaluation(
            agent_id=agent_id,
            eligible=eligible,
            reasons=reasons,
            scores=scores,
            policy_name=NotBlankStr("trend"),
            snapshot=snapshot,
            evaluated_at=now,
        )
