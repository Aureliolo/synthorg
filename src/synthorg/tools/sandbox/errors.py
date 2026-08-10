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
    exception rather than a result flag. Currently unused -- both
    subprocess and Docker return ``SandboxResult.timed_out`` instead.
    """


class SandboxStartError(SandboxError):
    """Failed to start the sandbox execution environment."""


class SandboxWorkspaceUnmappableError(SandboxError):
    """A containerised backend cannot describe its workspace to the daemon.

    Raised rather than falling back to the workspace path, because a path this
    process holds and the daemon does not resolves to a freshly created empty
    directory: the sandbox would start, see nothing, and fail every command for
    a reason unrelated to the command.
    """


class SandboxSubpathUnsupportedError(SandboxError):
    """The daemon is too old to mount the part of a volume the workspace is on.

    Mounting the whole volume instead is not the fallback: the subpath is what
    keeps one project's sandbox out of another project's files.
    """
