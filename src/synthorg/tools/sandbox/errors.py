"""Sandbox error hierarchy.

All sandbox errors inherit from ``ToolError`` so that sandbox failures
surface through the standard tool error path.
"""

from synthorg.tools.errors import ToolError


class SandboxError(ToolError):
    """Base exception for sandbox-layer errors."""


class SandboxTimeoutError(SandboxError):
    """Execution was killed because it exceeded the timeout.

    Reserved for sandbox backends that need to signal timeout as an
    exception rather than a result flag. Currently unused — both
    subprocess and Docker return ``SandboxResult.timed_out`` instead.
    """


class SandboxStartError(SandboxError):
    """Failed to start the sandbox execution environment."""
