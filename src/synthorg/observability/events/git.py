"""Git tool event constants."""

from typing import Final

GIT_COMMAND_START: Final[str] = "git.command.start"
GIT_COMMAND_SUCCESS: Final[str] = "git.command.success"
GIT_COMMAND_FAILED: Final[str] = "git.command.failed"
GIT_COMMAND_TIMEOUT: Final[str] = "git.command.timeout"
GIT_WORKSPACE_VIOLATION: Final[str] = "git.workspace.violation"
GIT_CLONE_URL_REJECTED: Final[str] = "git.clone.url_rejected"
GIT_CLONE_SSRF_BLOCKED: Final[str] = "git.clone.ssrf_blocked"
GIT_CLONE_DNS_FAILED: Final[str] = "git.clone.dns_failed"
GIT_CLONE_SSRF_DISABLED: Final[str] = "git.clone.ssrf_disabled"
GIT_CLONE_DNS_PINNED: Final[str] = "git.clone.dns_pinned"
GIT_CLONE_DNS_REBINDING_DETECTED: Final[str] = "git.clone.dns_rebinding_detected"
GIT_CLONE_TOCTOU_SKIPPED: Final[str] = "git.clone.toctou_skipped"
GIT_REF_INJECTION_BLOCKED: Final[str] = "git.ref.injection_blocked"

GIT_BACKEND_CONFIG_INVALID: Final[str] = "git.backend.config_invalid"
"""A git-backend factory rejected its config or a missing dependency."""
