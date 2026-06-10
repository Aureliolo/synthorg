"""Golden-benchmark signal aggregator.

Reads the learning curve recorded by the golden-company benchmark from
the configured scorecard history directory and summarises it into an
:class:`OrgBenchmarkSummary`. A missing/empty directory (no benchmark
configured, or no runs yet) yields an empty summary via the safe-default
path rather than raising, so the meta-loop degrades gracefully when the
offline benchmark has not run.

The benchmark is the org's ground-truth quality signal, so its latest
regression flag feeds the rule engine (see ``BenchmarkRegressionRule``),
turning a measured score drop into a corrective improvement proposal.
"""

import asyncio
from datetime import datetime
from pathlib import Path

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.meta.learning_curve import (
    DEFAULT_REGRESSION_THRESHOLD,
    read_learning_curve,
)
from synthorg.meta.signal_models import OrgBenchmarkSummary
from synthorg.observability import get_logger, log_exception_redacted
from synthorg.observability.events.meta import (
    META_SIGNAL_AGGREGATION_COMPLETED,
    META_SIGNAL_AGGREGATION_FAILED,
)

logger = get_logger(__name__)

_EMPTY = OrgBenchmarkSummary()


class BenchmarkSignalAggregator:
    """Aggregates the recorded benchmark curve into an org-wide summary.

    Args:
        history_dir: Directory the benchmark records per-run scorecard
            summaries into. When ``None`` (no ``meta.scorecard_history_dir``
            configured), aggregation yields an empty summary rather than
            failing.
        regression_threshold: Points a run must drop below its
            predecessor to count as a regression.
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
    def domain(self) -> NotBlankStr:
        """Signal domain name.

        Returns:
            ``NotBlankStr`` instance.
        """
        return NotBlankStr("benchmark")

    async def aggregate(
        self,
        *,
        since: datetime,
        until: datetime,
    ) -> OrgBenchmarkSummary:
        """Summarise the recorded benchmark curve.

        The benchmark curve spans every recorded run (per release /
        checkpoint), so the actionable signal -- whether the *latest*
        run regressed against its predecessor -- needs the last two
        points regardless of the observation window. ``since`` / ``until``
        are accepted for signal-aggregator protocol parity but do not
        window the sparse, offline benchmark history.

        Args:
            since: Start of observation window (unused; see above).
            until: End of observation window (unused; see above).

        Returns:
            Org-wide benchmark summary; empty when no history directory
            is configured or no runs have been recorded.
        """
        del since, until
        if self._history_dir is None:
            return _EMPTY
        try:
            curve = await asyncio.to_thread(
                read_learning_curve,
                self._history_dir,
                regression_threshold=self._threshold,
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            log_exception_redacted(
                logger, META_SIGNAL_AGGREGATION_FAILED, exc, domain="benchmark"
            )
            return _EMPTY

        if not curve.points:
            return _EMPTY

        latest = curve.points[-1]
        summary = OrgBenchmarkSummary(
            run_count=len(curve.points),
            latest_total=latest.total,
            max_total=latest.max_total,
            delta=latest.delta,
            is_regression=latest.is_regression,
            has_regression=curve.has_regression,
        )
        logger.info(
            META_SIGNAL_AGGREGATION_COMPLETED,
            domain="benchmark",
            run_count=summary.run_count,
            is_regression=summary.is_regression,
        )
        return summary
