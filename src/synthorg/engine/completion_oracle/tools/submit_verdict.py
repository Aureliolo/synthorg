# module-kind: code
"""``submit_completion_oracle_verdict`` tool implementation.

The single side effect of running the peer-review agent: the reviewer
files exactly one :class:`CompletionOracleReport` via this tool, which
writes it to the gate's :class:`CompletionOracleReportRepository` keyed by
``execution_id``.

The tool is constructed ONCE at boot in
:func:`synthorg.engine.completion_oracle.builder.build_completion_oracle_runtime`
with only ``report_repo`` bound, then registered on the agent engine's
shared tool registry. Per-evaluation identities flow through the trusted
runtime context, not the tool arguments, so the reviewer cannot spoof who
reviewed whom.
"""

from typing import ClassVar, Final, override

from pydantic import BaseModel, ValidationError

from synthorg.core.boundary import parse_typed
from synthorg.engine.completion_oracle.errors import (
    CompletionOracleVerdictAlreadyExistsError,
    CompletionOracleVerdictValidationError,
)
from synthorg.engine.completion_oracle.protocol import CompletionOracleReportRepository
from synthorg.engine.completion_oracle.review_models import CompletionOracleReport
from synthorg.engine.completion_oracle.runtime_context import (
    CompletionOracleRuntimeContext,
    get_completion_oracle_runtime_context,
)
from synthorg.engine.completion_oracle.tool_names import (
    SUBMIT_COMPLETION_ORACLE_VERDICT_TOOL_NAME,
)
from synthorg.engine.completion_oracle.tools._args import (
    SubmitCompletionOracleVerdictArgs,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.completion_oracle import (
    COMPLETION_ORACLE_VERDICT_DUPLICATE,
    COMPLETION_ORACLE_VERDICT_RECEIVED,
    COMPLETION_ORACLE_VERDICT_VALIDATION_FAILED,
)
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.base import BaseTool, ToolExecutionResult

logger = get_logger(__name__)

__all__ = [
    "SUBMIT_COMPLETION_ORACLE_VERDICT_TOOL_NAME",
    "SubmitCompletionOracleVerdictTool",
]

_TOOL_DESCRIPTION: Final[str] = (
    "Submit your independent completion-review verdict for the deliverable "
    "currently under review. Call this exactly once. Provide a non-empty "
    "summary and a verdict: approve, approve_with_notes, reject, or escalate. "
    "For a code deliverable you MUST build it and run its tests before "
    "approving; set ran_build / ran_tests and test_command accordingly. High "
    "and critical findings must carry at least one evidence quote. A reject "
    "MUST carry at least one finding: the rework brief is built from the "
    "findings, so a rejection without them sends the work back naming nothing."
)


class SubmitCompletionOracleVerdictTool(BaseTool):
    """The single tool exposed to the built-in completion-reviewer agent.

    Constructor-bound state:

    - ``report_repo``: where the parsed :class:`CompletionOracleReport` is
      written.

    The reviewer / executor identities are stamped from the trusted runtime
    context, keeping the tool a singleton on the engine's tool registry.
    """

    args_model: ClassVar[type[BaseModel] | None] = SubmitCompletionOracleVerdictArgs

    def __init__(
        self,
        *,
        report_repo: CompletionOracleReportRepository,
    ) -> None:
        super().__init__(
            name=SUBMIT_COMPLETION_ORACLE_VERDICT_TOOL_NAME,
            description=_TOOL_DESCRIPTION,
            # No declared action type, so the category's own default applies,
            # which is what the other two terminal submit tools take
            # (``submit_decomposition_plan``, ``submit_evaluation``). Naming
            # ``comms:internal`` here for want of a SecOps bucket put the one
            # act a judging session exists to perform behind the approval
            # SUPERVISED demands of anything leaving the sandbox. A verdict
            # leaves nothing: it is written to the archive the gate then
            # reads. The session is bounded, so the approval it was told to
            # wait for could never arrive inside it, and a live run spent 63
            # seconds proving that before the gate reported the reviewer had
            # filed nothing.
            category=ToolCategory.OTHER,
            parameters_schema=SubmitCompletionOracleVerdictArgs.model_json_schema(),
        )
        self._report_repo = report_repo

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Validate args, persist the :class:`CompletionOracleReport`, return ack.

        Returns:
            A ``ToolExecutionResult`` acknowledging the persisted verdict, or
            an error result on a duplicate submission.

        Raises:
            CompletionOracleVerdictValidationError: If the payload fails
                validation, is called outside a trusted runtime context, or
                its ids do not match that context.
        """
        args = self._parse_args(arguments)
        trusted = self._require_trusted_context(args)
        report = self._build_report(args, trusted)
        try:
            await self._report_repo.put(execution_id=args.execution_id, report=report)
        except CompletionOracleVerdictAlreadyExistsError as exc:
            logger.warning(
                COMPLETION_ORACLE_VERDICT_DUPLICATE,
                execution_id=args.execution_id,
                task_id=args.task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ToolExecutionResult(
                content=(
                    "A completion-oracle verdict has already been filed for "
                    "this execution. This tool is single-shot."
                ),
                is_error=True,
                metadata={"duplicate": True},
            )
        logger.info(
            COMPLETION_ORACLE_VERDICT_RECEIVED,
            execution_id=args.execution_id,
            task_id=args.task_id,
            verdict=report.verdict.value,
            findings=len(report.findings),
        )
        return ToolExecutionResult(
            content=(
                f"Filed completion-oracle verdict {report.verdict.value!r} with "
                f"{len(report.findings)} finding(s). The gate will now act on it."
            ),
            is_error=False,
            metadata={
                "execution_id": args.execution_id,
                "verdict": report.verdict.value,
            },
        )

    def _parse_args(
        self, arguments: dict[str, object]
    ) -> SubmitCompletionOracleVerdictArgs:
        """Validate the tool payload.

        Returns:
            The parsed arguments.

        Raises:
            CompletionOracleVerdictValidationError: On a structural mismatch.
        """
        try:
            return parse_typed(
                "agent.tool.submit_completion_oracle_verdict",
                arguments,
                SubmitCompletionOracleVerdictArgs,
            )
        except ValidationError as exc:
            logger.warning(
                COMPLETION_ORACLE_VERDICT_VALIDATION_FAILED,
                provided_keys=sorted(arguments.keys()),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "submit_completion_oracle_verdict payload failed validation"
            raise CompletionOracleVerdictValidationError(msg) from exc

    @staticmethod
    def _require_trusted_context(
        args: SubmitCompletionOracleVerdictArgs,
    ) -> CompletionOracleRuntimeContext:
        """Return the trusted context, rejecting a spoofed or absent one.

        Returns:
            The trusted :class:`CompletionOracleRuntimeContext`.

        Raises:
            CompletionOracleVerdictValidationError: If no trusted context is
                bound, or the args' ids do not match it.
        """
        trusted = get_completion_oracle_runtime_context()
        if trusted is None:
            logger.warning(
                COMPLETION_ORACLE_VERDICT_VALIDATION_FAILED,
                reason="no_trusted_context",
                supplied_execution_id=args.execution_id,
                supplied_task_id=args.task_id,
            )
            msg = (
                "submit_completion_oracle_verdict requires a trusted runtime "
                "context; it cannot be called outside a gate evaluation"
            )
            raise CompletionOracleVerdictValidationError(msg)
        if args.execution_id != trusted.execution_id or args.task_id != trusted.task_id:
            logger.warning(
                COMPLETION_ORACLE_VERDICT_VALIDATION_FAILED,
                reason="trusted_context_mismatch",
                supplied_execution_id=args.execution_id,
                supplied_task_id=args.task_id,
                trusted_execution_id=trusted.execution_id,
                trusted_task_id=trusted.task_id,
            )
            msg = (
                "submit_completion_oracle_verdict execution_id/task_id do not "
                "match the gate's trusted runtime context"
            )
            raise CompletionOracleVerdictValidationError(msg)
        return trusted

    @staticmethod
    def _build_report(
        args: SubmitCompletionOracleVerdictArgs,
        trusted: CompletionOracleRuntimeContext,
    ) -> CompletionOracleReport:
        """Construct the report from validated args + trusted identities.

        Returns:
            The frozen :class:`CompletionOracleReport`.

        Raises:
            CompletionOracleVerdictValidationError: If report construction
                fails validation (including the self-review guard).
        """
        try:
            return CompletionOracleReport(
                execution_id=trusted.execution_id,
                task_id=trusted.task_id,
                reviewer_agent_id=trusted.reviewer_agent_id,
                executor_agent_id=trusted.executor_agent_id,
                verdict=args.verdict,
                findings=args.findings,
                summary=args.summary,
                ran_build=args.ran_build,
                ran_tests=args.ran_tests,
                test_command=args.test_command,
            )
        except ValidationError as exc:
            logger.warning(
                COMPLETION_ORACLE_VERDICT_VALIDATION_FAILED,
                reason="report_model_validation",
                execution_id=args.execution_id,
                task_id=args.task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            # The reviewer sees this string as the tool's error observation and
            # can file again inside the same bounded session, so it carries the
            # rule that refused the report rather than the fact of refusal.
            msg = (
                "submit_completion_oracle_verdict report construction failed: "
                f"{safe_error_description(exc)}"
            )
            raise CompletionOracleVerdictValidationError(msg) from exc
