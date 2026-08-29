"""Code runner tool -- executes code snippets in a sandboxed environment.

Supports Python, JavaScript, and Bash via configurable sandbox backends.
"""

from pathlib import Path
from typing import ClassVar, Final, Literal, override

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.boundary import parse_typed
from synthorg.core.clock import Clock, SystemClock
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.code_runner import (
    CODE_RUNNER_EXECUTE_FAILED,
    CODE_RUNNER_EXECUTE_START,
    CODE_RUNNER_EXECUTE_SUCCESS,
)
from synthorg.persistence.code_execution_protocol import (
    CodeExecutionRecordRepository,
)
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools._shell_invocation import shell_invocation
from synthorg.tools._test_run_capture import record_if_test_run
from synthorg.tools._workspace_scope import require_project_id
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.sandbox.errors import SandboxError, agent_facing_message
from synthorg.tools.sandbox.protocol import SandboxBackend

logger = get_logger(__name__)

CodeRunnerLanguage = Literal["python", "javascript", "bash"]


class CodeRunnerArgs(BaseModel):
    """Args for ``code_runner``."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    code: str = Field(description="Source code to execute")
    language: CodeRunnerLanguage = Field(description="Programming language of the code")
    timeout: float | None = Field(
        default=None,
        ge=1,
        le=600,
        description="Optional timeout in seconds (minimum 1)",
    )


#: Interpreter languages only. Bash is absent because its invocation comes
#: from ``shell_invocation``, and a second spelling here would be the one an
#: editor changes while the executed command keeps its own flags.
_LANGUAGE_COMMANDS: Final[dict[str, tuple[str, str]]] = {
    "python": ("python3", "-c"),
    "javascript": ("node", "-e"),
}

#: The one language whose snippet IS a command line. A bash snippet running
#: ``pytest -q`` really did invoke the suite, so it is classified exactly as
#: the same line would be through ``shell_command``. A python or JavaScript
#: snippet is source text: classifying it would let a program that merely
#: contains the word "pytest" in a comment or a string mint evidence that a
#: suite passed, which is the forgery the classifier exists to prevent.
_SHELL_LANGUAGE: Final[str] = "bash"

#: Maximum characters of captured stdout/stderr kept on a test record.
_OUTPUT_TAIL_LIMIT: Final[int] = 2000
#: Maximum characters of the executed command kept on a test record.
_COMMAND_REPR_LIMIT: Final[int] = 500


class CodeRunnerTool(BaseTool):
    """Executes code snippets in a sandboxed environment.

    Supports Python, JavaScript, and Bash. Delegates execution to
    a ``SandboxBackend`` for isolation and resource control.
    """

    args_model: ClassVar[type[BaseModel] | None] = CodeRunnerArgs

    def __init__(
        self,
        *,
        sandbox: SandboxBackend | None,
        code_execution_records: CodeExecutionRecordRepository | None = None,
        clock: Clock | None = None,
        output_tail_limit: int = _OUTPUT_TAIL_LIMIT,
        workspace_root: Path | None = None,
    ) -> None:
        """Initialize the code runner tool.

        Args:
            sandbox: Sandboxed execution backend that enforces
                isolation and resource control. ``None`` when this
                deployment could not resolve one: the tool is still
                registered and refuses at invocation, naming the
                condition, rather than vanishing from the registry and
                leaving an agent to guess at tool names.
            code_execution_records: Optional repository for capturing a
                recognised test run into the deliverable receipt's
                provenance bundle. When ``None`` (or outside a bound
                execution scope) no record is written.
            clock: Clock seam for the capture record's ``executed_at``;
                defaults to ``SystemClock`` and is overridden with a
                ``FakeClock`` in tests.
            output_tail_limit: Maximum characters of captured
                stdout/stderr kept on a test record. Resolved from the
                ``tools.code_runner_output_tail_limit`` setting at the
                wiring boundary, defaulting to ``_OUTPUT_TAIL_LIMIT``.
            workspace_root: Base directory projects live under, so a run of a
                gate the project's own manifest declares (its linter, its
                formatter, its dependency check) is captured as the evidence
                the oracle requires rather than passing unrecorded.

        Raises:
            ValueError: When ``output_tail_limit`` is not a positive
                integer (a non-positive cap would defeat the tail slice
                and persist oversized output records).
        """
        super().__init__(
            name="code_runner",
            description=(
                "Executes code snippets in Python, JavaScript, "
                "or Bash within a sandboxed environment"
            ),
            category=ToolCategory.CODE_EXECUTION,
            parameters_schema=CodeRunnerArgs.model_json_schema(),
        )
        self._sandbox = sandbox
        self._code_execution_records = code_execution_records
        self._clock = clock or SystemClock()
        if not isinstance(output_tail_limit, int) or output_tail_limit <= 0:
            msg = (
                "output_tail_limit must be a positive integer, "
                f"got {output_tail_limit!r}"
            )
            raise ValueError(msg)
        self._output_tail_limit = output_tail_limit
        self._workspace_root = workspace_root

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Execute a code snippet in the sandbox.

        Args:
            arguments: Must contain ``code`` (str), ``language`` (str),
                and optionally ``timeout`` (float).

        Returns:
            A ``ToolExecutionResult`` with execution output.

        Raises:
            SandboxError: When the backend refuses on a condition no later run
                can clear. Raised rather than returned so the session ends on
                the infrastructure failure instead of retrying it.
        """
        # ``parse_typed`` validates ``language`` against the
        # ``CodeRunnerLanguage`` literal, so an out-of-set language is
        # rejected at the boundary and the command lookup below is total.
        args = parse_typed("tool.code_runner", arguments, CodeRunnerArgs)
        code = args.code
        language = args.language
        timeout = args.timeout

        if self._sandbox is None:
            # A deployment condition, not a bad call. Logged as well as
            # returned: this branch is where a tool plane that never came up
            # first meets an agent, and a silent refusal leaves nothing to
            # grep but the agent's own failure.
            logger.warning(
                CODE_RUNNER_EXECUTE_FAILED,
                language=language,
                reason="no_sandbox_backend",
            )
            return ToolExecutionResult(
                content=(
                    "This deployment wired no sandbox backend for code "
                    "execution, so no code can run here. Nothing about the "
                    "code caused this; see the 'agent_tool_execution' "
                    "subsystem for the condition."
                ),
                is_error=True,
                metadata={"language": language},
            )

        if language == _SHELL_LANGUAGE:
            # The same invocation ``shell_command`` uses, because a bash
            # snippet IS a command line and its exit status is read as
            # test evidence under the same rules.
            command, run_args = shell_invocation(code)
        else:
            program, flag = _LANGUAGE_COMMANDS[language]
            command, run_args = program, (flag, code)

        logger.debug(
            CODE_RUNNER_EXECUTE_START,
            language=language,
            timeout=timeout,
            code_length=len(code),
        )

        try:
            result = await self._sandbox.execute(
                command=command,
                args=run_args,
                timeout=timeout,
                category=self.category.value,
                project_id=require_project_id(),
            )
        except SandboxError as exc:
            # The log gets the operator's detail; the agent gets only what it
            # can act on, because a mount table in an LLM's context is
            # infrastructure reconnaissance it can be induced to relay.
            logger.warning(
                CODE_RUNNER_EXECUTE_FAILED,
                language=language,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
                retryable=exc.RETRYABLE,
            )
            # A condition no later run can clear is not the agent's to act on,
            # so it goes past the tool and ends the session as the
            # infrastructure failure it is, rather than being retried to the
            # budget ceiling against a sandbox that cannot answer.
            if not exc.RETRYABLE:
                raise
            return ToolExecutionResult(
                content=f"Sandbox error: {agent_facing_message(exc)}",
                is_error=True,
                metadata={"language": language},
            )

        if language == _SHELL_LANGUAGE:
            await record_if_test_run(
                result,
                command=code,
                records=self._code_execution_records,
                clock=self._clock,
                command_repr_limit=_COMMAND_REPR_LIMIT,
                output_tail_limit=self._output_tail_limit,
                workspace_root=self._workspace_root,
            )

        if result.success:
            logger.debug(
                CODE_RUNNER_EXECUTE_SUCCESS,
                language=language,
            )
            return ToolExecutionResult(
                content=result.stdout or "(no output)",
                metadata={
                    "returncode": result.returncode,
                    "language": language,
                },
            )

        logger.warning(
            CODE_RUNNER_EXECUTE_FAILED,
            language=language,
            returncode=result.returncode,
            timed_out=result.timed_out,
        )
        error_msg = result.stderr or result.stdout or "Execution failed"
        if result.timed_out:
            error_msg = f"Execution timed out. {error_msg}"
        return ToolExecutionResult(
            content=error_msg,
            is_error=True,
            metadata={
                "returncode": result.returncode,
                "timed_out": result.timed_out,
                "language": language,
            },
        )
