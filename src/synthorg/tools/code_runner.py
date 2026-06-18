"""Code runner tool -- executes code snippets in a sandboxed environment.

Supports Python, JavaScript, and Bash via configurable sandbox backends.
"""

from typing import ClassVar, Final, Literal, cast, override

from pydantic import BaseModel, ConfigDict, Field

from synthorg.core.clock import Clock, SystemClock
from synthorg.core.critical_errors import reraise_critical
from synthorg.core.execution_identity import current_execution_identity
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.code_runner import (
    CODE_RUNNER_EXECUTE_FAILED,
    CODE_RUNNER_EXECUTE_START,
    CODE_RUNNER_EXECUTE_SUCCESS,
    CODE_RUNNER_INVALID_LANGUAGE,
)
from synthorg.observability.events.deliverable_receipts import (
    TEST_RUN_RECORD_FAILED,
    TEST_RUN_RECORDED,
)
from synthorg.persistence.code_execution_protocol import (
    CodeExecutionPurpose,
    CodeExecutionRecord,
    CodeExecutionRecordRepository,
)
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.sandbox.errors import SandboxError
from synthorg.tools.sandbox.protocol import SandboxBackend
from synthorg.tools.sandbox.result import SandboxResult

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
    purpose: Literal["general", "tests"] = Field(
        default="general",
        description=(
            "Set to 'tests' when this run executes the project's test "
            "suite, so its structured result is captured for the "
            "deliverable's provenance receipt"
        ),
    )


_LANGUAGE_COMMANDS: Final[dict[str, tuple[str, str]]] = {
    "python": ("python3", "-c"),
    "javascript": ("node", "-e"),
    "bash": ("bash", "-c"),
}

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
        sandbox: SandboxBackend,
        code_execution_records: CodeExecutionRecordRepository | None = None,
        clock: Clock | None = None,
        output_tail_limit: int = _OUTPUT_TAIL_LIMIT,
    ) -> None:
        """Initialize the code runner tool.

        Args:
            sandbox: Sandboxed execution backend that enforces
                isolation and resource control.
            code_execution_records: Optional repository for capturing
                ``purpose='tests'`` runs into the deliverable receipt's
                provenance bundle. When ``None`` (or outside a bound
                execution scope) no record is written.
            clock: Clock seam for the capture record's ``executed_at``;
                defaults to ``SystemClock`` and is overridden with a
                ``FakeClock`` in tests.
            output_tail_limit: Maximum characters of captured
                stdout/stderr kept on a test record. Resolved from the
                ``tools.code_runner_output_tail_limit`` setting at the
                wiring boundary, defaulting to ``_OUTPUT_TAIL_LIMIT``.
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
        self._output_tail_limit = output_tail_limit

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
        """
        code = cast("str", arguments["code"])
        language = cast("str", arguments["language"])
        timeout = cast("float | None", arguments.get("timeout"))
        purpose = cast("str", arguments.get("purpose", "general"))

        if language not in _LANGUAGE_COMMANDS:
            logger.warning(
                CODE_RUNNER_INVALID_LANGUAGE,
                language=language,
            )
            return ToolExecutionResult(
                content=f"Unsupported language: {language!r}. "
                f"Supported: {sorted(_LANGUAGE_COMMANDS)}",
                is_error=True,
            )

        command, flag = _LANGUAGE_COMMANDS[language]

        logger.debug(
            CODE_RUNNER_EXECUTE_START,
            language=language,
            timeout=timeout,
            code_length=len(code),
        )

        try:
            result = await self._sandbox.execute(
                command=command,
                args=(flag, code),
                timeout=timeout,
            )
        except SandboxError as exc:
            logger.warning(
                CODE_RUNNER_EXECUTE_FAILED,
                language=language,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ToolExecutionResult(
                content=f"Sandbox error: {safe_error_description(exc)}",
                is_error=True,
                metadata={"language": language},
            )

        if purpose == "tests":
            await self._record_test_run(
                result,
                language=language,
                command=command,
                flag=flag,
                code=code,
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

    async def _record_test_run(
        self,
        result: SandboxResult,
        *,
        language: str,
        command: str,
        flag: str,
        code: str,
    ) -> None:
        """Capture a ``purpose='tests'`` run for the deliverable receipt.

        No-ops when no repository is wired or when called outside a
        bound execution scope. Best-effort: a capture failure logs and
        returns rather than failing the tool call.
        """
        if self._code_execution_records is None:
            return
        identity = current_execution_identity()
        if identity is None or identity.project_id is None:
            return
        command_repr = f"{command} {flag} {code}"[:_COMMAND_REPR_LIMIT]
        stdout_tail = (
            result.stdout[-self._output_tail_limit :] if result.stdout else None
        )
        stderr_tail = (
            result.stderr[-self._output_tail_limit :] if result.stderr else None
        )
        try:
            await self._code_execution_records.append(
                CodeExecutionRecord(
                    task_id=identity.task_id,
                    execution_id=identity.execution_id,
                    project_id=identity.project_id,
                    purpose=CodeExecutionPurpose.TESTS,
                    command=command_repr,
                    returncode=result.returncode,
                    passed=result.success,
                    timed_out=result.timed_out,
                    stdout_tail=stdout_tail,
                    stderr_tail=stderr_tail,
                    executed_at=self._clock.now(),
                )
            )
        except Exception as exc:  # noqa: BLE001 -- criticals re-raised
            reraise_critical(exc)
            logger.warning(
                TEST_RUN_RECORD_FAILED,
                execution_id=identity.execution_id,
                language=language,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return
        logger.debug(
            TEST_RUN_RECORDED,
            execution_id=identity.execution_id,
            task_id=identity.task_id,
            returncode=result.returncode,
            passed=result.success,
        )
