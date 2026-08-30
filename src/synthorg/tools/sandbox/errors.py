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
        RETRYABLE: Whether an identical command could ever succeed later. A
            retryable failure is handed to the agent as an ordinary error
            result and it decides what to do; a non-retryable one is raised
            past the tool, which ends the session as an infrastructure error.

            The distinction is not decoration. A condition the agent cannot
            clear, delivered as an ordinary tool result, reads exactly like a
            transient one, so the only sane behaviour available to the agent
            (try again, then try another route) spends its entire budget on a
            call that cannot succeed. Measured on a recorded sweep: six units
            each burned to a 1.5-million-token ceiling retrying ``ls`` against
            a sandbox that had shut down, wrote nothing, and were recorded as
            having built nothing.
    """

    AGENT_MESSAGE: ClassVar[str | None] = None
    RETRYABLE: ClassVar[bool] = True


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
    RETRYABLE: ClassVar[bool] = False


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
    # The flag `cleanup` sets is never cleared, so this backend refuses every
    # command for the rest of the process. Handed back as an ordinary error it
    # is indistinguishable from a transient one, and retrying it is what an
    # agent will do until something stops it.
    RETRYABLE: ClassVar[bool] = False


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
    RETRYABLE: ClassVar[bool] = False


class SandboxProjectScopeUnresolvedError(SandboxError):
    """No project could be resolved for a command that must run inside one.

    Raised rather than falling back to the workspace root. That root holds
    every project's files, so an unbound execution identity or a run with no
    project would silently widen a sandboxed command from one project to all of
    them, which is the isolation the per-project mount exists to provide.
    """

    AGENT_MESSAGE: ClassVar[str | None] = (
        "This command has no project to run inside, so it cannot be given a "
        "workspace. Nothing about the command caused this."
    )
    RETRYABLE: ClassVar[bool] = False


class SandboxBackgroundUnsupportedError(SandboxError):
    """This backend cannot start a detached, pollable background process.

    Raised by ``SubprocessSandbox``, which holds no per-owner container a
    background job's lifetime could tie to: every ``execute()`` call
    fully closes and communicates, with nothing persisting between two
    calls on the same owner. ``terminal``/``code_execution`` are
    force-routed to the Docker backend regardless of operator config
    (``UNTRUSTED_EXEC_CATEGORIES``), so this is reached only if that
    routing is ever bypassed; raised loudly rather than silently
    accepted and immediately orphaned.
    """

    AGENT_MESSAGE: ClassVar[str | None] = (
        "This sandbox backend cannot run a command in the background; it has "
        "no persistent container a background job could keep running in. "
        "Run the command in the foreground instead."
    )
    RETRYABLE: ClassVar[bool] = False


class SandboxBackgroundNoReusableContainerError(SandboxError):
    """The resolved lifecycle strategy has no persistent container to pin.

    Raised when ``background=True`` is requested under ``per-call``:
    that strategy destroys its container immediately after every single
    tool call, so a job started there would be orphaned by the very
    call that started it, before anything could ever check on it.
    """

    AGENT_MESSAGE: ClassVar[str | None] = (
        "This deployment's sandbox container is torn down after every "
        "command, so there is nothing for a background job to keep running "
        "in. Run the command in the foreground instead."
    )
    RETRYABLE: ClassVar[bool] = False


class SandboxBackgroundUnpinnedExecutionActiveError(SandboxError):
    """An unpinned foreground command is already running on this container.

    That command's own timeout can stop the container outright, which
    would collaterally kill a background job started while it is still
    in flight. Retryable: the agent may wait for the foreground command
    to finish (or time out) and try again.
    """

    AGENT_MESSAGE: ClassVar[str | None] = None
    RETRYABLE: ClassVar[bool] = True


class SandboxBackgroundJobLimitError(SandboxError):
    """The per-owner background-job cap is already reached.

    Retryable: the agent may cancel an existing job, wait for one to
    finish, or simply try again once headroom exists.
    """

    AGENT_MESSAGE: ClassVar[str | None] = None
    RETRYABLE: ClassVar[bool] = True


class SandboxBackgroundJobNotFoundError(SandboxError):
    """No background job matches the id a check/read/cancel call named.

    Covers both a genuinely unknown id and a job whose container was
    reclaimed (orphaned) before the caller acted on it.
    """

    AGENT_MESSAGE: ClassVar[str | None] = None
    RETRYABLE: ClassVar[bool] = False


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
    "SandboxBackgroundJobLimitError",
    "SandboxBackgroundJobNotFoundError",
    "SandboxBackgroundNoReusableContainerError",
    "SandboxBackgroundUnpinnedExecutionActiveError",
    "SandboxBackgroundUnsupportedError",
    "SandboxError",
    "SandboxProjectScopeUnresolvedError",
    "SandboxShuttingDownError",
    "SandboxStartError",
    "SandboxSubpathUnsupportedError",
    "SandboxTimeoutError",
    "SandboxWorkspaceUnmappableError",
    "agent_facing_message",
]
