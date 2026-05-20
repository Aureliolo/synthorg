"""``submit_red_team_report`` tool implementation.

The single side effect of running the red-team agent: the agent files
exactly one :class:`RedTeamReport` via this tool, which writes it to
the gate's :class:`RedTeamReportRepository` keyed by ``execution_id``.

The tool is constructed ONCE at boot in
:func:`synthorg.security.redteam.builder.build_red_team_runtime` with
only ``report_repo`` bound to it, then registered on the agent engine's
shared tool registry. ``execution_id`` and ``task_id`` flow through
the tool arguments payload (echoed by the agent from the system
prompt's brief block), so the same tool instance serves every red-team
evaluation without per-evaluation construction overhead.
"""

from typing import Any, ClassVar, Final

from pydantic import BaseModel, ValidationError

from synthorg.api.boundary import parse_typed
from synthorg.core.enums import ToolCategory
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.red_team import (
    RED_TEAM_FINDING_FILED,
    RED_TEAM_REPORT_RECEIVED,
    RED_TEAM_REPORT_VALIDATION_FAILED,
)
from synthorg.security.redteam.errors import (
    RedTeamReportAlreadyExistsError,
    RedTeamReportValidationError,
)
from synthorg.security.redteam.models import RedTeamReport
from synthorg.security.redteam.protocol import RedTeamReportRepository  # noqa: TC001
from synthorg.security.redteam.runtime_context import (
    get_red_team_runtime_context,
)
from synthorg.security.redteam.tools._args import SubmitRedTeamReportArgs
from synthorg.tools.base import BaseTool, ToolExecutionResult

logger = get_logger(__name__)

SUBMIT_RED_TEAM_REPORT_TOOL_NAME: Final[str] = "submit_red_team_report"
"""Canonical tool name. Used by the gate prompt and by tests."""

_TOOL_DESCRIPTION: Final[str] = (
    "Submit your adversarial red-team review report for the deliverable "
    "currently under review. Call this exactly once. Provide a non-empty "
    "summary; supply zero or more findings, each tagged with attack_surface "
    "(correctness | security | requirements | grounding) and severity "
    "(info | low | medium | high | critical). High and critical findings "
    "must carry at least one evidence quote from the deliverable."
)

_TOOL_ACTION_TYPE: Final[str] = "comms:internal"
"""Maps the tool into the existing comms taxonomy.

Filing a structured critique is an internal communication artefact;
keeping it under ``comms:internal`` avoids creating a new action-type
category for a single tool while still giving SecOps a known bucket.
"""


class SubmitRedTeamReportTool(BaseTool):
    """The single tool exposed to the built-in red-team agent.

    Constructor-bound state:

    - ``report_repo``: where the parsed :class:`RedTeamReport` is written.

    The agent supplies ``execution_id`` and ``task_id`` via the args
    payload, echoed from the system prompt's brief block. This keeps
    the tool a singleton on the engine's tool registry (registered
    once at boot, just like every other tool in the codebase).
    """

    args_model: ClassVar[type[BaseModel] | None] = SubmitRedTeamReportArgs

    def __init__(
        self,
        *,
        report_repo: RedTeamReportRepository,
    ) -> None:
        super().__init__(
            name=SUBMIT_RED_TEAM_REPORT_TOOL_NAME,
            description=_TOOL_DESCRIPTION,
            category=ToolCategory.OTHER,
            action_type=_TOOL_ACTION_TYPE,
            parameters_schema=SubmitRedTeamReportArgs.model_json_schema(),
        )
        self._report_repo = report_repo

    async def execute(
        self,
        *,
        arguments: dict[str, Any],
    ) -> ToolExecutionResult:
        """Validate args, persist the :class:`RedTeamReport`, return ack."""
        try:
            args = parse_typed(
                "agent.tool.submit_red_team_report",
                arguments,
                SubmitRedTeamReportArgs,
            )
        except ValidationError as exc:
            logger.warning(
                RED_TEAM_REPORT_VALIDATION_FAILED,
                provided_keys=sorted(arguments.keys()),
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            msg = "submit_red_team_report payload failed validation"
            raise RedTeamReportValidationError(msg) from exc

        trusted_ctx = get_red_team_runtime_context()
        if trusted_ctx is not None and (
            args.execution_id != trusted_ctx.execution_id
            or args.task_id != trusted_ctx.task_id
        ):
            logger.warning(
                RED_TEAM_REPORT_VALIDATION_FAILED,
                reason="trusted_context_mismatch",
                supplied_execution_id=args.execution_id,
                supplied_task_id=args.task_id,
                trusted_execution_id=trusted_ctx.execution_id,
                trusted_task_id=trusted_ctx.task_id,
            )
            msg = (
                "submit_red_team_report execution_id/task_id do not match "
                "the gate's trusted runtime context"
            )
            raise RedTeamReportValidationError(msg)

        report = RedTeamReport(
            execution_id=args.execution_id,
            task_id=args.task_id,
            findings=args.findings,
            summary=args.summary,
        )
        try:
            await self._report_repo.put(
                execution_id=args.execution_id,
                report=report,
            )
        except RedTeamReportAlreadyExistsError as exc:
            logger.warning(
                RED_TEAM_REPORT_RECEIVED,
                execution_id=args.execution_id,
                task_id=args.task_id,
                duplicate=True,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ToolExecutionResult(
                content=(
                    "A red-team report has already been filed for this "
                    "execution. submit_red_team_report is single-shot."
                ),
                is_error=True,
                metadata={"duplicate": True},
            )

        logger.info(
            RED_TEAM_REPORT_RECEIVED,
            execution_id=args.execution_id,
            task_id=args.task_id,
            findings=len(report.findings),
        )
        for finding in report.findings:
            logger.info(
                RED_TEAM_FINDING_FILED,
                execution_id=args.execution_id,
                task_id=args.task_id,
                attack_surface=finding.attack_surface.value,
                severity=finding.severity.value,
                source=finding.source,
            )
        return ToolExecutionResult(
            content=(
                f"Filed red-team report with {len(report.findings)} "
                f"finding(s). Evaluation will now compute the gate verdict."
            ),
            is_error=False,
            metadata={
                "execution_id": args.execution_id,
                "findings": len(report.findings),
            },
        )
