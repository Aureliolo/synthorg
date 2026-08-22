# module-kind: adapter
"""The planning session's one terminal tool, and the sink it writes into.

A planning session's whole deliverable is a single call to
``submit_decomposition_plan``, so the tool and the holder the strategy reads
the plan back out of travel together. A malformed or style-refused submission
comes back as a tool error rather than an exception, which is what lets the
session correct and resubmit on its next turn instead of ending on it.
"""

from typing import cast, override

from pydantic import JsonValue

from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.llm_parse import args_to_decomposition_plan
from synthorg.engine.decomposition.llm_prompt import build_decomposition_tool
from synthorg.engine.decomposition.models import DecompositionPlan
from synthorg.engine.errors import DecompositionError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.decomposition import (
    DECOMPOSITION_SESSION_DUPLICATE_SUBMIT,
)
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.base import BaseTool, ToolExecutionResult

logger = get_logger(__name__)


class PlanCapture:
    """Mutable holder for the plan a session submits via the terminal tool."""

    __slots__ = ("plan",)

    def __init__(self) -> None:
        self.plan: DecompositionPlan | None = None


class SubmitDecompositionPlanTool(BaseTool):
    """Terminal planning tool: the session submits its final plan through it.

    The schema mirrors the single-shot decomposition tool (so each subtask
    carries ``expected_artifacts`` + ``acceptance_criteria``); the parsed,
    id-remapped plan is captured for the strategy to return. A malformed
    submission surfaces as a tool error so the agent can correct and resubmit
    within the same session.
    """

    def __init__(
        self,
        *,
        parent_task_id: NotBlankStr,
        capture: PlanCapture,
        available_roles: tuple[NotBlankStr, ...] = (),
    ) -> None:
        super().__init__(
            name="submit_decomposition_plan",
            description=(
                "Submit the final plan. Provide every item with its "
                "dependencies (only genuine ones, so independent work runs in "
                "parallel), an accountable owning role, calibrated stakes, "
                "expected_artifacts, and acceptance_criteria. Call this exactly "
                "once, last, after you have researched and self-reviewed."
            ),
            parameters_schema=build_decomposition_tool(
                available_roles
            ).parameters_schema,
            category=ToolCategory.OTHER,
        )
        self._parent_task_id = parent_task_id
        self._capture = capture
        self._available_roles = available_roles

    @override
    async def execute(
        self,
        *,
        arguments: dict[str, object],
    ) -> ToolExecutionResult:
        """Parse + capture the submitted plan, or report a correctable error.

        Returns:
            A success result naming the accepted subtask count, or an error
            result describing why the plan was rejected so the agent retries.
        """
        try:
            plan = args_to_decomposition_plan(
                cast("dict[str, JsonValue]", arguments),
                self._parent_task_id,
                self._available_roles,
            )
        except DecompositionError as exc:
            return ToolExecutionResult(
                content=(
                    f"Plan rejected: {safe_error_description(exc)}. "
                    "Fix the issue and call submit_decomposition_plan again."
                ),
                is_error=True,
            )
        if self._capture.plan is not None:
            logger.warning(
                DECOMPOSITION_SESSION_DUPLICATE_SUBMIT,
                parent_task_id=self._parent_task_id,
                previous_subtask_count=len(self._capture.plan.subtasks),
                new_subtask_count=len(plan.subtasks),
            )
        self._capture.plan = plan
        return ToolExecutionResult(
            content=(
                f"Plan accepted with {len(plan.subtasks)} subtasks. You may stop now."
            ),
        )


__all__ = ["PlanCapture", "SubmitDecompositionPlanTool"]
