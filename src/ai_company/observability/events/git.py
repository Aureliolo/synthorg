"""Git tool event constants."""

from typing import Final

GIT_COMMAND_START: Final[str] = "git.command.start"
GIT_COMMAND_SUCCESS: Final[str] = "git.command.success"
GIT_COMMAND_FAILED: Final[str] = "git.command.failed"
GIT_COMMAND_TIMEOUT: Final[str] = "git.command.timeout"
GIT_WORKSPACE_VIOLATION: Final[str] = "git.workspace.violation"
GIT_CLONE_URL_REJECTED: Final[str] = "git.clone.url_rejected"
GIT_REF_INJECTION_BLOCKED: Final[str] = "git.ref.injection_blocked"
