"""Performance tracking event constants for structured logging.

Constants follow the ``perf.<subject>.<action>`` naming convention
and are passed as the first argument to structured log calls.
"""

from typing import Final

PERF_METRIC_RECORDED: Final[str] = "perf.metric.recorded"
PERF_METRIC_PERSIST_FAILED: Final[str] = "perf.metric.persist_failed"
PERF_TRACKER_CLEARED: Final[str] = "perf.tracker.cleared"
PERF_INFLECTION_SINK_BOUND: Final[str] = "perf.inflection_sink.bound"
PERF_INFLECTION_SINK_CLEARED: Final[str] = "perf.inflection_sink.cleared"
PERF_INFLECTION_SINK_BIND_REJECTED: Final[str] = "perf.inflection_sink.bind_rejected"
PERF_BACKGROUND_TASK_FAILED: Final[str] = "perf.background_task.failed"
PERF_QUALITY_SCORED: Final[str] = "perf.quality.scored"
PERF_COLLABORATION_SCORED: Final[str] = "perf.collaboration.scored"
PERF_SNAPSHOT_COMPUTED: Final[str] = "perf.snapshot.computed"
PERF_SNAPSHOT_FAILED: Final[str] = "perf.snapshot.failed"
PERF_TREND_COMPUTED: Final[str] = "perf.trend.computed"
PERF_WINDOW_INSUFFICIENT_DATA: Final[str] = "perf.window.insufficient_data"

# ── LLM calibration sampling ─────────────────────────────────
PERF_LLM_SAMPLE_STARTED: Final[str] = "perf.llm_sample.started"
PERF_LLM_SAMPLE_COMPLETED: Final[str] = "perf.llm_sample.completed"
PERF_LLM_SAMPLE_FAILED: Final[str] = "perf.llm_sample.failed"

# ── Collaboration score overrides ─────────────────────────────
PERF_OVERRIDE_SET: Final[str] = "perf.override.set"
PERF_OVERRIDE_CLEARED: Final[str] = "perf.override.cleared"
PERF_OVERRIDE_APPLIED: Final[str] = "perf.override.applied"
PERF_OVERRIDE_EXPIRED: Final[str] = "perf.override.expired"

# ── Quality score overrides ──────────────────────────────────
PERF_QUALITY_OVERRIDE_SET: Final[str] = "perf.quality_override.set"
PERF_QUALITY_OVERRIDE_CLEARED: Final[str] = "perf.quality_override.cleared"
PERF_QUALITY_OVERRIDE_APPLIED: Final[str] = "perf.quality_override.applied"
PERF_QUALITY_OVERRIDE_EXPIRED: Final[str] = "perf.quality_override.expired"

# ── LLM quality judge ────────────────────────────────────────
PERF_LLM_JUDGE_STARTED: Final[str] = "perf.llm_judge.started"
PERF_LLM_JUDGE_COMPLETED: Final[str] = "perf.llm_judge.completed"
PERF_LLM_JUDGE_FAILED: Final[str] = "perf.llm_judge.failed"

# ── LLM quality judge cost ──────────────────────────────────
PERF_JUDGE_COST_RECORDING_FAILED: Final[str] = "perf.judge_cost.recording_failed"

# ── Composite quality scoring ────────────────────────────────
PERF_COMPOSITE_SCORED: Final[str] = "perf.composite_quality.scored"
PERF_COMPOSITE_RETRY_EXHAUSTED: Final[str] = "perf.composite_quality.retry_exhausted"
