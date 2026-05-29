"""Code runner tool -- executes code snippets in a sandboxed environment.

Supports Python, JavaScript, and Bash via configurable sandbox backends.
"""

from typing import TYPE_CHECKING, Any, ClassVar, Final, override

from pydantic import BaseModel

from synthorg.core.enums import ToolCategory
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.code_runner import (
    CODE_RUNNER_EXECUTE_FAILED,
    CODE_RUNNER_EXECUTE_START,
    CODE_RUNNER_EXECUTE_SUCCESS,
    CODE_RUNNER_INVALID_LANGUAGE,
)
from synthorg.tools._misc_args import CodeRunnerArgs
from synthorg.tools.base import BaseTool, ToolExecutionResult
from synthorg.tools.sandbox.errors import SandboxError

if TYPE_CHECKING:
    from synthorg.tools.sandbox.protocol import SandboxBackend

logger = get_logger(__name__)

_LANGUAGE_COMMANDS: Final[dict[str, tuple[str, str]]] = {
    "python": ("python3", "-c"),
    "javascript": ("node", "-e"),
    "bash": ("bash", "-c"),
}


class CodeRunnerTool(BaseTool):
    """Executes code snippets in a sandboxed environment.

    Supports Python, JavaScript, and Bash. Delegates execution to
    a ``SandboxBackend`` for isolation and resource control.
    """

    args_model: ClassVar[type[BaseModel] | None] = CodeRunnerArgs

    def __init__(self, *, sandbox: SandboxBackend) -> None:
        """Initialize the code runner tool.

        Args:
            sandbox: Sandboxed execution backend that enforces
                isolation and resource control.
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

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Execute a code snippet in the sandbox.

        Args:
            arguments: Must contain ``code`` (str), ``language`` (str),
                and optionally ``timeout`` (float).

        Returns:
            A ``ToolExecutionResult`` with execution output.
        """
        code: str = arguments["code"]
        language: str = arguments["language"]
        timeout: float | None = arguments.get("timeout")

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
