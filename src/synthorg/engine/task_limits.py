# module-kind: declarative
"""Single source of truth for task field-length caps.

Shared by the task-engine create/update models, the blocking-delegation
spec, and the delegate-tool args model so a change to a cap cannot silently
desynchronise the spec-time and task-creation-time validations.
"""

from typing import Final

MAX_TASK_TITLE_LENGTH: Final[int] = 256
"""Maximum length for a task title (matches the API ``CreateTaskRequest``)."""

MAX_TASK_DESCRIPTION_LENGTH: Final[int] = 4096
"""Maximum length for a task description (matches the API ``CreateTaskRequest``)."""
