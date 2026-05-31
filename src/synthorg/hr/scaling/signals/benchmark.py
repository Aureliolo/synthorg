"""Benchmark signal source -- reads the golden-benchmark learning curve.

Surfaces the org's ground-truth quality signal into the scaling subsystem so
hiring/scaling strategies can react to a measured score drop: a regressing
benchmark is an org-wide instability signal that should make the company hold
its team rather than prune capacity while quality is already falling. Mirrors
the meta-loop's ``BenchmarkSignalAggregator`` (same curve, different consumer).

A missing/empty history directory (no benchmark configured, or no runs yet)
yields neutral, non-regression signals via the safe-default path rather than
raising, so scaling degrades gracefully when the offline benchmark has not run.
"""

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.hr.scaling.models import ScalingSignal
from synthorg.meta.learning_curve import (
    DEFAULT_REGRESSION_THRESHOLD,
    read_learning_curve,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.hr import HR_SCALING_SIGNAL_COLLECTION_DEGRADED

logger = get_logger(__name__)

_SOURCE_NAME: Final[NotBlankStr] = NotBlankStr("benchmark")

# Signal names shared with the consuming strategies (so the producer and the
# ``PerformancePruningStrategy`` regression guard agree on the contract).
BENCHMARK_REGRESSION_SIGNAL: Final[NotBlankStr] = NotBlankStr("benchmark_is_regression")
BENCHMARK_TREND_SIGNAL: Final[NotBlankStr] = NotBlankStr("benchmark_score_trend")

# Numeric encoding of the boolean regression flag for the float signal value.
_REGRESSION_TRUE: Final[float] = 1.0
_REGRESSION_FALSE: Final[float] = 0.0


class BenchmarkSignalSource:
    """Read-only adapter over the recorded benchmark learning curve.

    Emits the latest run's score trend (delta vs the previous run) and a
    regression flag derived from the same curve the meta-loop reads.

    Args:
        history_dir: Directory the benchmark records per-run scorecard
            summaries into. When ``None`` (no ``meta.scorecard_history_dir``
            configured), collection yields neutral, non-regression signals.
        regression_threshold: Points a run must drop below its predecessor to
            count as a regression.
    """

    def __init__(
        self,
        history_dir: Path | None = None,
        *,
        regression_threshold: int = DEFAULT_REGRESSION_THRESHOLD,
    ) -> None:
        self._history_dir = history_dir
        self._threshold = regression_threshold

    @property
    def name(self) -> NotBlankStr:
        """Source identifier.

        Returns:
            The source name (``"benchmark"``).
        """
        return _SOURCE_NAME

    async def collect(
        self,
        agent_ids: tuple[NotBlankStr, ...],
    ) -> tuple[ScalingSignal, ...]:
        """Collect benchmark trend + regression signals.

        The curve is org-wide, not per-agent, so ``agent_ids`` is accepted for
        protocol parity but not consulted.

        Args:
            agent_ids: Active agent IDs (unused; the benchmark is org-wide).

        Returns:
            ``(benchmark_score_trend, benchmark_is_regression)`` signals;
            neutral (trend 0, no regression) when no history is configured or
            no runs have been recorded.
        """
        del agent_ids
        now = datetime.now(UTC)
        if self._history_dir is None:
            return self._neutral(now)
        try:
            curve = await asyncio.to_thread(
                read_learning_curve,
                self._history_dir,
                regression_threshold=self._threshold,
            )
        except Exception as exc:
            reraise_critical(exc)
            logger.warning(
                HR_SCALING_SIGNAL_COLLECTION_DEGRADED,
                source="benchmark",
                action="collection_failed",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return self._neutral(now)

        if not curve.points:
            return self._neutral(now)

        latest = curve.points[-1]
        return (
            ScalingSignal(
                name=BENCHMARK_TREND_SIGNAL,
                value=float(latest.delta),
                source=_SOURCE_NAME,
                timestamp=now,
            ),
            ScalingSignal(
                name=BENCHMARK_REGRESSION_SIGNAL,
                value=_REGRESSION_TRUE if latest.is_regression else _REGRESSION_FALSE,
                source=_SOURCE_NAME,
                timestamp=now,
            ),
        )

    @staticmethod
    def _neutral(now: datetime) -> tuple[ScalingSignal, ...]:
        """Return neutral (zero-trend, non-regression) benchmark signals.

        Returns:
            The default ``(trend=0, regression=0)`` signal pair.
        """
        return (
            ScalingSignal(
                name=BENCHMARK_TREND_SIGNAL,
                value=_REGRESSION_FALSE,
                source=_SOURCE_NAME,
                timestamp=now,
            ),
            ScalingSignal(
                name=BENCHMARK_REGRESSION_SIGNAL,
                value=_REGRESSION_FALSE,
                source=_SOURCE_NAME,
                timestamp=now,
            ),
        )
