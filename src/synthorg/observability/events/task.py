"""Task event constants."""

from typing import Final

TASK_CREATED: Final[str] = "task.created"
TASK_STATUS_CHANGED: Final[str] = "task.status.changed"
TASK_TRANSITION: Final[str] = "task.transition"
TASK_TRANSITION_INVALID: Final[str] = "task.transition.invalid"
TASK_TRANSITION_CONFIG_ERROR: Final[str] = "task.transition.config_error"
TASK_ACTIVITY_PUBLISH_FAILED: Final[str] = "task.activity.publish_failed"
TASK_ACTIVITY_OUTCOME_RESOLVE_FAILED: Final[str] = (
    "task.activity.outcome_resolve_failed"
)
TASK_ACTIVITY_METRIC_RECORD_FAILED: Final[str] = "task.activity.metric_record_failed"
