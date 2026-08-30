"""Check / read / cancel / list tools for a ``shell_command`` background job.

Siblings of ``shell_command.py`` rather than an addition to it, the same
way ``code_runner.py`` sits beside it: a distinct tool family, kept out
of the file that starts a job to respect the module-size budget.
"""

import json
from typing import ClassVar, Final, override

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from synthorg.core.boundary import parse_typed
from synthorg.core.types import NotBlankStr
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.terminal import TERMINAL_COMMAND_FAILED
from synthorg.security.autonomy.enums import ActionType
from synthorg.settings.resolver_protocol import ConfigResolverProtocol
from synthorg.tools.base import ToolExecutionResult
from synthorg.tools.sandbox.errors import SandboxError, agent_facing_message
from synthorg.tools.sandbox.protocol import SandboxBackend
from synthorg.tools.terminal.base_terminal_tool import BaseTerminalTool
from synthorg.tools.terminal.config import TerminalConfig

logger = get_logger(__name__)

#: Default cap on bytes read back by ``read_background_job_output`` when
#: the caller does not name a smaller one. Mirrors ``shell_command``'s own
#: ``max_output_bytes`` default (see ``TerminalConfig``); a background
#: job's own capture is already bounded at write time by the operator's
#: ``shell_command_background_output_byte_cap`` setting, so this is a
#: second, independent cap on the READ, not a resize of the capture.
_DEFAULT_READ_BYTE_CAP: Final[int] = 1_000_000


def _invalid_arguments_result(exc: ValidationError) -> ToolExecutionResult:
    """Log and translate an args-validation failure into a tool error.

    Returns:
        An error ``ToolExecutionResult`` naming the missing/invalid field.
    """
    logger.warning(
        TERMINAL_COMMAND_FAILED,
        reason="invalid_arguments",
        error_type=type(exc).__name__,
        error=safe_error_description(exc),
    )
    return ToolExecutionResult(
        content=f"Invalid arguments: {safe_error_description(exc)}",
        is_error=True,
    )


def _sandbox_error_result(exc: SandboxError) -> ToolExecutionResult:
    """Translate a caught ``SandboxError`` into a tool error result.

    Returns:
        An error ``ToolExecutionResult`` carrying the agent-facing text.
    """
    logger.warning(
        TERMINAL_COMMAND_FAILED,
        error_type=type(exc).__name__,
        error=safe_error_description(exc),
        retryable=exc.RETRYABLE,
    )
    return ToolExecutionResult(
        content=f"Sandbox error: {agent_facing_message(exc)}",
        is_error=True,
    )


class CheckBackgroundJobArgs(BaseModel):
    """Args for ``check_background_job``."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    job_id: NotBlankStr = Field(description="Job id returned by shell_command")


class CheckBackgroundJobTool(BaseTerminalTool):
    """Report a background job's current status and exit code."""

    args_model: ClassVar[type[BaseModel] | None] = CheckBackgroundJobArgs

    def __init__(
        self,
        *,
        sandbox: SandboxBackend | None = None,
        config: TerminalConfig | None = None,
        config_resolver: ConfigResolverProtocol | None = None,
    ) -> None:
        """Initialize the tool over the shared terminal sandbox wiring."""
        super().__init__(
            name="check_background_job",
            description=(
                "Check a backgrounded shell_command job's status and, once "
                "it has finished, its exit code."
            ),
            parameters_schema=CheckBackgroundJobArgs.model_json_schema(),
            action_type=ActionType.CODE_READ.value,
            sandbox=sandbox,
            config=config,
            config_resolver=config_resolver,
        )

    @override
    async def execute(self, *, arguments: dict[str, object]) -> ToolExecutionResult:
        """Poll the job and report its status.

        Returns:
            A ``ToolExecutionResult`` with the job's status/exit code.
        """
        try:
            args = parse_typed(
                "tool.check_background_job", arguments, CheckBackgroundJobArgs
            )
        except ValidationError as exc:
            return _invalid_arguments_result(exc)
        if self._sandbox is None:
            return ToolExecutionResult(
                content="This deployment wired no sandbox backend.",
                is_error=True,
            )
        try:
            record = await self._sandbox.poll_background(args.job_id)
        except SandboxError as exc:
            return _sandbox_error_result(exc)
        return ToolExecutionResult(
            content=json.dumps(
                {
                    "job_id": record.job_id,
                    "status": record.status.value,
                    "exit_code": record.exit_code,
                }
            ),
        )


class ReadBackgroundJobOutputArgs(BaseModel):
    """Args for ``read_background_job_output``."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    job_id: NotBlankStr = Field(description="Job id returned by shell_command")
    byte_cap: int = Field(
        default=_DEFAULT_READ_BYTE_CAP,
        gt=0,
        description="Maximum bytes of output to return",
    )


class ReadBackgroundJobOutputTool(BaseTerminalTool):
    """Read a background job's captured output so far."""

    args_model: ClassVar[type[BaseModel] | None] = ReadBackgroundJobOutputArgs

    def __init__(
        self,
        *,
        sandbox: SandboxBackend | None = None,
        config: TerminalConfig | None = None,
        config_resolver: ConfigResolverProtocol | None = None,
    ) -> None:
        """Initialize the tool over the shared terminal sandbox wiring."""
        super().__init__(
            name="read_background_job_output",
            description=(
                "Read a backgrounded shell_command job's captured output "
                "(stdout+stderr, interleaved), whether it is still running "
                "or has finished."
            ),
            parameters_schema=ReadBackgroundJobOutputArgs.model_json_schema(),
            action_type=ActionType.CODE_READ.value,
            sandbox=sandbox,
            config=config,
            config_resolver=config_resolver,
        )

    @override
    async def execute(self, *, arguments: dict[str, object]) -> ToolExecutionResult:
        """Read the job's captured output.

        Returns:
            A ``ToolExecutionResult`` with the captured text.
        """
        try:
            args = parse_typed(
                "tool.read_background_job_output",
                arguments,
                ReadBackgroundJobOutputArgs,
            )
        except ValidationError as exc:
            return _invalid_arguments_result(exc)
        if self._sandbox is None:
            return ToolExecutionResult(
                content="This deployment wired no sandbox backend.",
                is_error=True,
            )
        try:
            output = await self._sandbox.read_background_output(
                args.job_id, byte_cap=args.byte_cap
            )
        except SandboxError as exc:
            return _sandbox_error_result(exc)
        return ToolExecutionResult(content=output or "(no output yet)")


class CancelBackgroundJobArgs(BaseModel):
    """Args for ``cancel_background_job``."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    job_id: NotBlankStr = Field(description="Job id returned by shell_command")


class CancelBackgroundJobTool(BaseTerminalTool):
    """Terminate a running background job.

    Keeps the terminal category's default action type (``code:write``):
    unlike its three read-only siblings, this terminates a running,
    write-capable process.
    """

    args_model: ClassVar[type[BaseModel] | None] = CancelBackgroundJobArgs

    def __init__(
        self,
        *,
        sandbox: SandboxBackend | None = None,
        config: TerminalConfig | None = None,
        config_resolver: ConfigResolverProtocol | None = None,
    ) -> None:
        """Initialize the tool over the shared terminal sandbox wiring."""
        super().__init__(
            name="cancel_background_job",
            description=(
                "Cancel a running backgrounded shell_command job. A job "
                "that has already finished is left alone; cancelling it "
                "is not an error."
            ),
            parameters_schema=CancelBackgroundJobArgs.model_json_schema(),
            sandbox=sandbox,
            config=config,
            config_resolver=config_resolver,
        )

    @override
    async def execute(self, *, arguments: dict[str, object]) -> ToolExecutionResult:
        """Cancel the job.

        Returns:
            A ``ToolExecutionResult`` with the job's status after
            cancellation.
        """
        try:
            args = parse_typed(
                "tool.cancel_background_job", arguments, CancelBackgroundJobArgs
            )
        except ValidationError as exc:
            return _invalid_arguments_result(exc)
        if self._sandbox is None:
            return ToolExecutionResult(
                content="This deployment wired no sandbox backend.",
                is_error=True,
            )
        try:
            record = await self._sandbox.cancel_background(args.job_id)
        except SandboxError as exc:
            return _sandbox_error_result(exc)
        return ToolExecutionResult(
            content=json.dumps(
                {"job_id": record.job_id, "status": record.status.value}
            ),
        )


class ListBackgroundJobsArgs(BaseModel):
    """Args for ``list_background_jobs``."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    owner_id: NotBlankStr | None = Field(
        default=None,
        description=(
            "Lifecycle owner to list jobs for. Omit to list this agent's "
            "own jobs (the default and the common case)."
        ),
    )


class ListBackgroundJobsTool(BaseTerminalTool):
    """List background jobs recorded for an owner, newest-first."""

    args_model: ClassVar[type[BaseModel] | None] = ListBackgroundJobsArgs

    def __init__(
        self,
        *,
        sandbox: SandboxBackend | None = None,
        config: TerminalConfig | None = None,
        config_resolver: ConfigResolverProtocol | None = None,
    ) -> None:
        """Initialize the tool over the shared terminal sandbox wiring."""
        super().__init__(
            name="list_background_jobs",
            description=(
                "List backgrounded shell_command jobs, newest-first. "
                "Defaults to this agent's own jobs."
            ),
            parameters_schema=ListBackgroundJobsArgs.model_json_schema(),
            action_type=ActionType.CODE_READ.value,
            sandbox=sandbox,
            config=config,
            config_resolver=config_resolver,
        )

    @override
    async def execute(self, *, arguments: dict[str, object]) -> ToolExecutionResult:
        """List the owner's background jobs.

        Returns:
            A ``ToolExecutionResult`` with the job list.
        """
        try:
            args = parse_typed(
                "tool.list_background_jobs", arguments, ListBackgroundJobsArgs
            )
        except ValidationError as exc:
            return _invalid_arguments_result(exc)
        if self._sandbox is None:
            return ToolExecutionResult(content=json.dumps({"jobs": []}))
        try:
            jobs = await self._sandbox.list_background_jobs(
                args.owner_id, category=self.category.value
            )
        except SandboxError as exc:
            return _sandbox_error_result(exc)
        return ToolExecutionResult(
            content=json.dumps(
                {
                    "jobs": [
                        {
                            "job_id": j.job_id,
                            "status": j.status.value,
                            "command_repr": j.command_repr,
                            "started_at": j.started_at.isoformat(),
                        }
                        for j in jobs
                    ],
                },
            ),
        )


__all__ = [
    "CancelBackgroundJobArgs",
    "CancelBackgroundJobTool",
    "CheckBackgroundJobArgs",
    "CheckBackgroundJobTool",
    "ListBackgroundJobsArgs",
    "ListBackgroundJobsTool",
    "ReadBackgroundJobOutputArgs",
    "ReadBackgroundJobOutputTool",
]
