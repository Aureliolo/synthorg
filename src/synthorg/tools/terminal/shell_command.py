"""Shell command tool -- execute commands in a sandboxed environment.

Delegates to a ``SandboxBackend`` for isolated execution.  Commands
are validated against allow/blocklist before execution.  Output is
truncated at ``max_output_bytes``.
"""

import json
from pathlib import Path
from typing import ClassVar, Final, override

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from synthorg.core.boundary import parse_typed
from synthorg.core.clock import Clock, SystemClock
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.terminal import (
    TERMINAL_COMMAND_FAILED,
    TERMINAL_COMMAND_START,
    TERMINAL_COMMAND_SUCCESS,
    TERMINAL_COMMAND_TIMEOUT,
)
from synthorg.persistence.code_execution_protocol import (
    CodeExecutionRecordRepository,
)
from synthorg.settings.resolver_protocol import ConfigResolverProtocol
from synthorg.tools._shell_invocation import shell_invocation
from synthorg.tools._test_run_capture import record_if_test_run
from synthorg.tools._workspace_scope import require_project_id
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.sandbox.errors import SandboxError, agent_facing_message
from synthorg.tools.sandbox.protocol import SandboxBackend
from synthorg.tools.terminal._settings import (
    resolve_shell_command_background_enabled,
    resolve_shell_command_background_max_duration_seconds,
    resolve_shell_command_timeout,
)
from synthorg.tools.terminal.base_terminal_tool import BaseTerminalTool
from synthorg.tools.terminal.config import TerminalConfig

logger = get_logger(__name__)

#: Maximum characters of the command kept on a test record.
_COMMAND_REPR_LIMIT: Final[int] = 500

#: Maximum characters of captured stdout/stderr kept on a test record.
_OUTPUT_TAIL_LIMIT: Final[int] = 2000

#: Fallback for ``shell_command_background_enabled`` when the resolver is
#: unavailable; matches the setting's own registered default.
_DEFAULT_BACKGROUND_ENABLED: Final[bool] = True

#: Fallback for ``shell_command_background_max_duration_seconds`` when the
#: resolver is unavailable; matches the setting's own registered default.
_DEFAULT_BACKGROUND_MAX_DURATION_SECONDS: Final[float] = 3600.0


class ShellCommandArgs(BaseModel):
    """Args for ``shell_command``.

    Allowlist / blocklist enforcement and ``working_directory`` policy
    stay inside the tool body because they depend on per-instance
    sandbox configuration.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    command: NotBlankStr = Field(description="Shell command to execute")
    working_directory: NotBlankStr | None = Field(
        default=None,
        description="Working directory (relative to workspace)",
    )
    timeout: float | None = Field(
        default=None,
        ge=1,
        le=600,
        description="Command timeout in seconds",
    )
    background: bool = Field(
        default=False,
        description=(
            "Run detached instead of waiting for completion. Returns a "
            "job_id immediately; check_background_job / "
            "read_background_job_output / cancel_background_job address "
            "it on a later turn. Governed by its own duration ceiling, "
            "not timeout -- the two are mutually exclusive."
        ),
    )

    @model_validator(mode="after")
    def _timeout_requires_foreground(self) -> ShellCommandArgs:
        """A backgrounded job has no 1-600s foreground timeout to honour.

        Its own duration ceiling governs instead
        (``shell_command_background_max_duration_seconds``), so
        combining the two fields would be ambiguous about which one
        actually applies.

        Returns:
            ``self``, unchanged.

        Raises:
            ValueError: Both ``background`` and ``timeout`` were given.
        """
        if self.background and self.timeout is not None:
            msg = (
                "timeout is a foreground-only setting; a backgrounded "
                "command is governed by its own duration ceiling instead"
            )
            raise ValueError(msg)
        return self


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

    def __init__(
        self,
        *,
        sandbox: SandboxBackend | None = None,
        config: TerminalConfig | None = None,
        config_resolver: ConfigResolverProtocol | None = None,
        code_execution_records: CodeExecutionRecordRepository | None = None,
        clock: Clock | None = None,
        output_tail_limit: int = _OUTPUT_TAIL_LIMIT,
        workspace_root: Path | None = None,
    ) -> None:
        """Initialize the shell command tool.

        Args:
            sandbox: Sandboxed execution backend.
            config: Terminal-tool configuration with allowlist /
                blocklist and timeouts.
            config_resolver: Live settings resolver, read per command for
                the ceilings an operator can change. ``None`` keeps
                *config*'s own values for the life of the tool.
            code_execution_records: Optional repository the deliverable
                receipt reads. A suite run here is the same evidence as one
                run through ``code_runner``, so which tool the agent
                happened to pick stops deciding whether the build/test
                oracle has anything to judge.
            clock: Clock seam for the receipt's ``executed_at``.
            output_tail_limit: Maximum characters of captured stdout/stderr
                kept on a test record.
            workspace_root: Base directory projects live under, so a run of a
                gate the project's own manifest declares (its linter, its
                formatter, its dependency check) is captured as the evidence
                the oracle requires rather than passing unrecorded.

        Raises:
            ValueError: When ``output_tail_limit`` is not positive; a
                non-positive cap would defeat the tail slice and persist
                unbounded output.
        """
        super().__init__(
            name="shell_command",
            description=(
                "Execute a shell command in a sandboxed environment. "
                "Output is captured and returned."
            ),
            parameters_schema=ShellCommandArgs.model_json_schema(),
            sandbox=sandbox,
            config=config,
            config_resolver=config_resolver,
        )
        self._code_execution_records = code_execution_records
        self._clock: Clock = clock or SystemClock()
        if output_tail_limit <= 0:
            msg = (
                "output_tail_limit must be a positive integer, "
                f"got {output_tail_limit!r}"
            )
            raise ValueError(msg)
        self._output_tail_limit = output_tail_limit
        self._workspace_root = workspace_root

    @staticmethod
    def _validate_working_dir(
        working_dir: str | None,
    ) -> ToolExecutionResult | Path | None:
        """Validate and resolve the working directory.

        Returns ``None`` when no working dir is specified, a ``Path``
        for valid relative paths, or a ``ToolExecutionResult`` error
        for absolute or traversal paths.

        Returns:
            The resulting ``ToolExecutionResult | Path``, or ``None`` when unavailable.
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

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Execute a shell command.

        Args:
            arguments: Must contain ``command``; optionally
                ``working_directory`` and ``timeout``.

        Returns:
            A ``ToolExecutionResult`` with command output.
        """
        try:
            args = parse_typed("tool.shell_command", arguments, ShellCommandArgs)
        except ValidationError as exc:
            logger.warning(
                TERMINAL_COMMAND_FAILED,
                reason="invalid_arguments",
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ToolExecutionResult(
                content=(
                    "Invalid shell command arguments: a non-empty command is required"
                ),
                is_error=True,
            )
        command = args.command
        working_dir = args.working_directory

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
            # A deployment condition, not a bad call: say so, or the agent
            # spends turns rephrasing a command that was never going to run.
            # Logged as well as returned, because this branch is where a tool
            # plane that died AFTER boot first shows itself, and a silent
            # refusal here leaves nothing to grep but the agent's own failure.
            logger.warning(
                TERMINAL_COMMAND_FAILED,
                command=command,
                reason="no_sandbox_backend",
            )
            return ToolExecutionResult(
                content=(
                    "This deployment wired no sandbox backend for terminal "
                    "execution, so no shell command can run here. Nothing "
                    "about the command caused this; see the "
                    "'agent_tool_execution' subsystem for the condition."
                ),
                is_error=True,
            )

        if args.background:
            return await self._execute_background(command, working_dir)

        # The call's own timeout wins; otherwise the ceiling in force RIGHT
        # NOW, read live, so an operator who raises it after watching an
        # install time out does not have to restart anything to find out
        # whether the new value is enough.
        timeout: float = (
            args.timeout
            if args.timeout is not None
            else await resolve_shell_command_timeout(
                self._config_resolver,
                fallback=self._config.default_timeout,
            )
        )
        logger.info(
            TERMINAL_COMMAND_START,
            command=command,
            timeout=timeout,
        )

        return await self._execute_sandboxed(command, timeout, working_dir)

    async def _execute_background(
        self,
        command: str,
        working_dir: str | None = None,
    ) -> ToolExecutionResult:
        """Start *command* detached in the sandbox and return its job id.

        Args:
            command: Shell command to execute.
            working_dir: Optional working directory path.

        Returns:
            A ``ToolExecutionResult`` whose content is
            ``{"job_id": ...}`` on success.

        Raises:
            RuntimeError: If the operation fails at runtime.
            SandboxError: When the backend refuses on a condition no
                later command can clear. Raised rather than returned so
                the session ends on the infrastructure failure instead
                of retrying it.
        """
        if self._sandbox is None:  # pragma: no cover -- guarded by caller
            msg = "_execute_background called without sandbox"
            raise RuntimeError(msg)

        if not await resolve_shell_command_background_enabled(
            self._config_resolver, fallback=_DEFAULT_BACKGROUND_ENABLED
        ):
            return ToolExecutionResult(
                content=(
                    "Backgrounded shell commands are disabled on this "
                    "deployment (tools.shell_command_background_enabled)."
                ),
                is_error=True,
            )

        cwd_or_error = self._validate_working_dir(working_dir)
        if isinstance(cwd_or_error, ToolExecutionResult):
            return cwd_or_error
        cwd = cwd_or_error

        max_duration_seconds = (
            await resolve_shell_command_background_max_duration_seconds(
                self._config_resolver, fallback=_DEFAULT_BACKGROUND_MAX_DURATION_SECONDS
            )
        )
        try:
            # Unlike `_execute_sandboxed`, the raw command text is passed
            # straight through rather than run through `shell_invocation`:
            # `start_background` hands `command` to `build_start_command`
            # as a single shell line it wraps in its own `bash -c` layer
            # (the wrapper script itself needs to embed a line, not an
            # argv). Pre-wrapping it here would double-wrap: the wrapper
            # would then embed `bash -o pipefail -c <command>` as ITS
            # script text, and bash's `-c` semantics only ever run the
            # first token after `-c`, silently discarding every argument
            # after it -- corrupting any command with a space in it.
            #
            # owner_id deliberately omitted: `None` derives the SAME
            # owner an unscoped foreground `execute()` call under this
            # category would use (see `start_background`'s own
            # docstring), so the job lands in the container this
            # agent's other terminal calls already use.
            job_id = await self._sandbox.start_background(
                command=command,
                args=(),
                cwd=cwd,
                category=self.category.value,
                project_id=require_project_id(),
                max_duration_seconds=max_duration_seconds,
            )
        except SandboxError as exc:
            logger.warning(
                TERMINAL_COMMAND_FAILED,
                command=command,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                retryable=exc.RETRYABLE,
            )
            if not exc.RETRYABLE:
                raise
            return ToolExecutionResult(
                content=f"Sandbox error: {agent_facing_message(exc)}",
                is_error=True,
            )

        logger.info(
            TERMINAL_COMMAND_START,
            command=command,
            background=True,
        )
        return ToolExecutionResult(content=json.dumps({"job_id": job_id}))

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

        Raises:
            RuntimeError: If the operation fails at runtime.
            SandboxError: When the backend refuses on a condition no later
                command can clear. Raised rather than returned so the session
                ends on the infrastructure failure instead of retrying it.
        """
        if self._sandbox is None:  # pragma: no cover -- guarded by caller
            msg = "_execute_sandboxed called without sandbox"
            raise RuntimeError(msg)

        cwd_or_error = self._validate_working_dir(working_dir)
        if isinstance(cwd_or_error, ToolExecutionResult):
            return cwd_or_error
        cwd = cwd_or_error

        program, args = shell_invocation(command)
        try:
            result = await self._sandbox.execute(
                command=program,
                args=args,
                cwd=cwd,
                timeout=timeout,
                category=self.category.value,
                project_id=require_project_id(),
            )
        except SandboxError as exc:
            # The log gets the operator's detail; the agent gets only what it
            # can act on, because a mount table in an LLM's context is
            # infrastructure reconnaissance it can be induced to relay.
            logger.warning(
                TERMINAL_COMMAND_FAILED,
                command=command,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                retryable=exc.RETRYABLE,
            )
            # A condition no later command can clear is not the agent's to act
            # on, so it goes past the tool and ends the session as the
            # infrastructure failure it is. Returned as a result it reads like
            # a transient error, and the agent then spends its whole budget
            # retrying a command that cannot succeed.
            if not exc.RETRYABLE:
                raise
            return ToolExecutionResult(
                content=f"Sandbox error: {agent_facing_message(exc)}",
                is_error=True,
            )

        # Recorded before the timeout branch returns: a suite that timed out
        # is evidence the build is not verified, and dropping it would read
        # as no attempt rather than a failed one.
        await record_if_test_run(
            result,
            command=command,
            records=self._code_execution_records,
            clock=self._clock,
            command_repr_limit=_COMMAND_REPR_LIMIT,
            output_tail_limit=self._output_tail_limit,
            workspace_root=self._workspace_root,
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
