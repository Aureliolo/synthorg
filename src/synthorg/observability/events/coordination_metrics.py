"""Coordination metrics event constants."""

from typing import Final

COORD_METRICS_AMDAHL_COMPUTED: Final[str] = "coordination.metrics.amdahl_computed"
COORD_METRICS_STRAGGLER_GAP_COMPUTED: Final[str] = (
    "coordination.metrics.straggler_gap_computed"
)
COORD_METRICS_TOKEN_SPEEDUP_ALERT: Final[str] = (
    "coordination.metrics.token_speedup_alert"  # noqa: S105
)
COORD_METRICS_MESSAGE_OVERHEAD_ALERT: Final[str] = (
    "coordination.metrics.message_overhead_alert"
)
COORD_METRICS_VALIDATION_ERROR: Final[str] = "coordination.metrics.validation_error"

# Runtime collection pipeline events
COORD_METRICS_EFFICIENCY_COMPUTED: Final[str] = (
    "coordination.metrics.efficiency_computed"
)
COORD_METRICS_OVERHEAD_COMPUTED: Final[str] = "coordination.metrics.overhead_computed"
COORD_METRICS_ERROR_AMPLIFICATION_COMPUTED: Final[str] = (
    "coordination.metrics.error_amplification_computed"
)
COORD_METRICS_MESSAGE_DENSITY_COMPUTED: Final[str] = (
    "coordination.metrics.message_density_computed"
)
COORD_METRICS_REDUNDANCY_COMPUTED: Final[str] = (
    "coordination.metrics.redundancy_computed"
)
COORD_METRICS_COLLECTION_STARTED: Final[str] = "coordination.metrics.collection_started"
COORD_METRICS_COLLECTION_COMPLETED: Final[str] = (
    "coordination.metrics.collection_completed"
)
COORD_METRICS_COLLECTION_FAILED: Final[str] = "coordination.metrics.collection_failed"
COORD_METRICS_BASELINE_RECORDED: Final[str] = "coordination.metrics.baseline_recorded"
COORD_METRICS_BASELINE_INSUFFICIENT: Final[str] = (
    "coordination.metrics.baseline_insufficient"
)
COORD_METRICS_ALERT_FIRED: Final[str] = "coordination.metrics.alert_fired"
COORD_METRICS_STORE_CLEARED: Final[str] = "coordination.metrics.store_cleared"

# Read-side (MCP facade) events
COORD_METRICS_RECORD_FETCHED: Final[str] = "coordination.metrics.record_fetched"
COORD_METRICS_INVALID_REQUEST: Final[str] = "coordination.metrics.invalid_request"
