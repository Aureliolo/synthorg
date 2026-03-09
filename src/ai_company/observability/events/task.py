"""Task event constants."""

from typing import Final

TASK_CREATED: Final[str] = "task.created"
TASK_STATUS_CHANGED: Final[str] = "task.status.changed"
TASK_TRANSITION_INVALID: Final[str] = "task.transition.invalid"
TASK_TRANSITION_CONFIG_ERROR: Final[str] = "task.transition.config_error"
