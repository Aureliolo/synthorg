"""Performance tracking event constants for structured logging.

Constants follow the ``perf.<subject>.<action>`` naming convention
and are passed as the first argument to structured log calls.
"""

from typing import Final

PERF_METRIC_RECORDED: Final[str] = "perf.metric.recorded"
PERF_METRIC_PERSIST_FAILED: Final[str] = "perf.metric.persist_failed"
PERF_TRACKER_CLEARED: Final[str] = "perf.tracker.cleared"
PERF_AGENT_FORGOTTEN: Final[str] = "perf.tracker.agent_forgotten"
PERF_INFLECTION_SINK_BOUND: Final[str] = "perf.inflection_sink.bound"
PERF_INFLECTION_SINK_CLEARED: Final[str] = "perf.inflection_sink.cleared"
PERF_INFLECTION_SINK_BIND_REJECTED: Final[str] = "perf.inflection_sink.bind_rejected"
PERF_BACKGROUND_TASK_FAILED: Final[str] = "perf.background_task.failed"
PERF_SNAPSHOT_COMPUTED: Final[str] = "perf.snapshot.computed"
PERF_SNAPSHOT_FAILED: Final[str] = "perf.snapshot.failed"
PERF_TREND_COMPUTED: Final[str] = "perf.trend.computed"
PERF_WINDOW_INSUFFICIENT_DATA: Final[str] = "perf.window.insufficient_data"
PERF_TASK_OUTCOMES_TRACKER_UNWIRED: Final[str] = "perf.task_outcomes.tracker_unwired"
