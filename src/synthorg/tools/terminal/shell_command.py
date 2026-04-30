"""Shell command tool -- execute commands in a sandboxed environment.

Delegates to a ``SandboxBackend`` for isolated execution.  Commands
are validated against allow/blocklist before execution.  Output is
truncated at ``max_output_bytes``.
"""

from pathlib import Path
from typing import Any, ClassVar

from pydantic import BaseModel  # noqa: TC002 -- ClassVar type at runtime

from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.terminal import (
    TERMINAL_COMMAND_FAILED,
    TERMINAL_COMMAND_START,
    TERMINAL_COMMAND_SUCCESS,
    TERMINAL_COMMAND_TIMEOUT,
)
from synthorg.tools._misc_args import ShellCommandArgs
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.sandbox.errors import SandboxError
from synthorg.tools.terminal.base_terminal_tool import BaseTerminalTool

logger = get_logger(__name__)


class ShellCommandTool(BaseTerminalTool):
    """Execute shell commands in a sandboxed environment.

    Commands are validated against the allow/blocklist before
    execution.  Output (stdout + stderr) is captured and truncated
    at ``max_output_bytes``.

    When no sandbox backend is provided, returns an error (terminal
    tools require sandboxed execution).

    Examples:
        Execute a command::

            tool = ShellCommandTool(sandbox=my_sandbox)
            result = await tool.execute(arguments={"command": "ls -la"})
    """

    args_model: ClassVar[type[BaseModel] | None] = ShellCommandArgs

    def __init__(self, **kwargs: Any) -> None:
        """Initialize the shell command tool.

        Args:
            **kwargs: Forwarded to :class:`BaseTerminalTool`. Typically
                includes ``sandbox`` (sandboxed execution backend) and
                ``config`` (terminal-tool configuration with
                allowlist/blocklist + timeouts).
        """
        super().__init__(
            name="shell_command",
            description=(
                "Execute a shell command in a sandboxed environment. "
                "Output is captured and returned."
            ),
            parameters_schema=ShellCommandArgs.model_json_schema(),
            **kwargs,
        )

    @staticmethod
    def _validate_working_dir(
        working_dir: str | None,
    ) -> ToolExecutionResult | Path | None:
        """Validate and resolve the working directory.

        Returns ``None`` when no working dir is specified, a ``Path``
        for valid relative paths, or a ``ToolExecutionResult`` error
        for absolute or traversal paths.
        """
        if not working_dir:
            return None

        cwd = Path(working_dir)
        if cwd.is_absolute():
            return ToolExecutionResult(
                content=(
                    f"Absolute working_directory not allowed: {working_dir!r}. "
                    "Use a path relative to the workspace."
                ),
                is_error=True,
            )
        try:
            resolved = (Path.cwd() / cwd).resolve()
            if not resolved.is_relative_to(Path.cwd().resolve()):
                return ToolExecutionResult(
                    content=(
                        f"Path traversal not allowed: {working_dir!r} "
                        "escapes the workspace."
                    ),
                    is_error=True,
                )
        except ValueError, OSError:
            return ToolExecutionResult(
                content=f"Invalid working_directory: {working_dir!r}",
                is_error=True,
            )
        return cwd

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Execute a shell command.

        Args:
            arguments: Must contain ``command``; optionally
                ``working_directory`` and ``timeout``.

        Returns:
            A ``ToolExecutionResult`` with command output.
        """
        command: str = arguments["command"]
        working_dir: str | None = arguments.get("working_directory")
        raw_timeout = arguments.get("timeout")
        timeout: float = (
            raw_timeout if raw_timeout is not None else self._config.default_timeout
        )

        if not command.strip():
            return ToolExecutionResult(
                content="Empty command",
                is_error=True,
            )

        # Blocklist check first (higher priority than allowlist)
        if self._is_command_blocked(command):
            return ToolExecutionResult(
                content=f"Command blocked by security policy: {command!r}",
                is_error=True,
            )

        # Allowlist check
        if not self._is_command_allowed(command):
            return ToolExecutionResult(
                content=(
                    f"Command not in allowlist: {command!r}. "
                    f"Allowed prefixes: {self._config.command_allowlist}"
                ),
                is_error=True,
            )

        if self._sandbox is None:
            return ToolExecutionResult(
                content=(
                    "Terminal tools require a sandbox backend. "
                    "No sandbox is configured."
                ),
                is_error=True,
            )

        logger.info(
            TERMINAL_COMMAND_START,
            command=command,
            timeout=timeout,
        )

        return await self._execute_sandboxed(command, timeout, working_dir)

    async def _execute_sandboxed(
        self,
        command: str,
        timeout: float,  # noqa: ASYNC109  -- passed to sandbox, not asyncio
        working_dir: str | None = None,
    ) -> ToolExecutionResult:
        """Execute the command through the sandbox backend.

        Args:
            command: Shell command to execute.
            timeout: Execution timeout in seconds.
            working_dir: Optional working directory path.

        Returns:
            A ``ToolExecutionResult`` with the output.
        """
        if self._sandbox is None:  # pragma: no cover -- guarded by caller
            msg = "_execute_sandboxed called without sandbox"
            raise RuntimeError(msg)

        cwd_or_error = self._validate_working_dir(working_dir)
        if isinstance(cwd_or_error, ToolExecutionResult):
            return cwd_or_error
        cwd = cwd_or_error

        try:
            result = await self._sandbox.execute(
                command="bash",
                args=("-c", command),
                cwd=cwd,
                timeout=timeout,
            )
        except SandboxError as exc:
            logger.warning(
                TERMINAL_COMMAND_FAILED,
                command=command,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ToolExecutionResult(
                content=f"Sandbox error: {exc}",
                is_error=True,
            )

        if result.timed_out:
            logger.warning(
                TERMINAL_COMMAND_TIMEOUT,
                command=command,
                timeout=timeout,
            )
            return ToolExecutionResult(
                content=f"Command timed out after {timeout}s",
                is_error=True,
                metadata={"timed_out": True},
            )

        # Combine stdout and stderr
        output = result.stdout or ""
        if result.stderr:
            if output:
                output += "\n"
            output += result.stderr

        # Truncate by bytes, not characters.
        truncated = False
        out_bytes = output.encode("utf-8")
        if len(out_bytes) > self._config.max_output_bytes:
            truncated = True
            marker = (
                f"\n\n[Truncated: output exceeded"
                f" {self._config.max_output_bytes:,} bytes]"
            )
            marker_bytes = marker.encode("utf-8")
            limit = max(0, self._config.max_output_bytes - len(marker_bytes))
            output = out_bytes[:limit].decode("utf-8", errors="ignore") + marker

        if result.success:
            logger.info(
                TERMINAL_COMMAND_SUCCESS,
                command=command,
                returncode=result.returncode,
                output_length=len(output),
            )
        else:
            logger.warning(
                TERMINAL_COMMAND_FAILED,
                command=command,
                returncode=result.returncode,
            )

        return ToolExecutionResult(
            content=output or "(no output)",
            is_error=not result.success,
            metadata={
                "returncode": result.returncode,
                "timed_out": result.timed_out,
                "truncated": truncated,
            },
        )
