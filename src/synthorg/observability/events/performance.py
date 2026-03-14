"""Performance tracking event constants for structured logging.

Constants follow the ``perf.<subject>.<action>`` naming convention
and are passed as the first argument to structured log calls.
"""

from typing import Final

PERF_METRIC_RECORDED: Final[str] = "perf.metric.recorded"
PERF_QUALITY_SCORED: Final[str] = "perf.quality.scored"
PERF_COLLABORATION_SCORED: Final[str] = "perf.collaboration.scored"
PERF_SNAPSHOT_COMPUTED: Final[str] = "perf.snapshot.computed"
PERF_TREND_COMPUTED: Final[str] = "perf.trend.computed"
PERF_WINDOW_INSUFFICIENT_DATA: Final[str] = "perf.window.insufficient_data"
