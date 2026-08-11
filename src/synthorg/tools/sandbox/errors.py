"""Sandbox error hierarchy.

All sandbox errors inherit from ``ToolError`` so that sandbox failures
surface through the standard tool error path.

Some of these are raised where the reader is an operator and caught where the
reader is a language model. A message written for the first names container
ids, workspace paths, the mount table and the daemon version, all of which are
infrastructure reconnaissance once they land in an agent's context and can be
relayed onward by any tool that agent holds. So a message meant only for the
operator declares an ``AGENT_MESSAGE`` alongside it, and every agent-facing
sink renders :func:`agent_facing_message` rather than the exception itself.
"""

from typing import ClassVar

from synthorg.observability import safe_error_description
from synthorg.tools.errors import ToolError


class SandboxError(ToolError):
    """Base exception for sandbox-layer errors.

    Attributes:
        AGENT_MESSAGE: What an agent may be told instead of this error's own
            text. ``None`` means the text is already safe to hand over.
    """

    AGENT_MESSAGE: ClassVar[str | None] = None


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

    The operator-facing text names this container, its workspace root and every
    mount it holds, so an agent is told the consequence and where an operator
    can read the cause.
    """

    AGENT_MESSAGE: ClassVar[str | None] = (
        "This deployment cannot give a sandbox the project's files, so no "
        "command can run against them. Nothing about the command caused this; "
        "see the 'agent_tool_execution' subsystem for the condition."
    )


class SandboxShuttingDownError(SandboxError):
    """The backend is tearing down, so no new command may start.

    Raised rather than admitted and abandoned: teardown closes the daemon
    client, and a command let through after that point fails partway with a
    transport error naming the session rather than the shutdown, leaving a
    container behind that nothing is left tracking.
    """

    AGENT_MESSAGE: ClassVar[str | None] = (
        "This deployment's sandbox is shutting down, so no command can be "
        "started. Nothing about the command caused this."
    )


class SandboxSubpathUnsupportedError(SandboxError):
    """The daemon is too old to mount the part of a volume the workspace is on.

    Mounting the whole volume instead is not the fallback: the subpath is what
    keeps one project's sandbox out of another project's files.
    """

    AGENT_MESSAGE: ClassVar[str | None] = (
        "This deployment's container runtime is too old to isolate one "
        "project's files from another's, so no command can run against them. "
        "Nothing about the command caused this; see the "
        "'agent_tool_execution' subsystem for the condition."
    )


def agent_facing_message(exc: SandboxError) -> str:
    """Return what an agent may be told about *exc*.

    Args:
        exc: The sandbox failure being reported.

    Returns:
        The error's declared agent-facing text, or its redacted description
        when the error carries nothing an agent should not see.
    """
    return exc.AGENT_MESSAGE or safe_error_description(exc)


__all__ = [
    "SandboxError",
    "SandboxShuttingDownError",
    "SandboxStartError",
    "SandboxSubpathUnsupportedError",
    "SandboxTimeoutError",
    "SandboxWorkspaceUnmappableError",
    "agent_facing_message",
]
