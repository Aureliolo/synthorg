# module-kind: adapter
"""The planning session's one terminal tool, and the sink it writes into.

A planning session's whole deliverable is a single call to
``submit_decomposition_plan``, so the tool and the holder the strategy reads
the plan back out of travel together. A malformed or style-refused submission
comes back as a tool error rather than an exception, which is what lets the
session correct and resubmit on its next turn instead of ending on it.
"""

import asyncio
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
    DECOMPOSITION_SESSION_PLAN_REJECTED,
)
from synthorg.security.autonomy.enums import ToolCategory
from synthorg.tools.base import BaseTool, ToolExecutionResult

logger = get_logger(__name__)


class PlanCapture:
    """The one plan a session submits, and the only writer of it.

    The check-and-set lives here rather than in the tool because it has to be
    one step: a turn is free to emit two ``submit_decomposition_plan`` calls,
    the invoker runs them as siblings in one task group against this one
    instance, and a check in the tool would let both see an empty capture, so
    the duplicate goes unreported and which plan survives is whichever task
    finished last.

    Read-only from outside for the same reason the write is guarded: the
    session's loop asks this object whether it has a plan yet, and anything
    able to clear it could put a delivered session back to undelivered.

    Args:
        parent_task_id: The objective being planned, for the duplicate warning.
    """

    __slots__ = ("_lock", "_parent_task_id", "_plan")

    def __init__(self, parent_task_id: NotBlankStr) -> None:
        self._plan: DecompositionPlan | None = None
        self._parent_task_id = parent_task_id
        self._lock = asyncio.Lock()

    @property
    def plan(self) -> DecompositionPlan | None:
        """The plan submitted so far, or ``None`` while none has been."""
        return self._plan

    async def set(self, plan: DecompositionPlan) -> None:
        """Accept *plan*, reporting it when it supersedes another.

        Args:
            plan: The plan the session just submitted.
        """
        async with self._lock:
            if self._plan is not None:
                logger.warning(
                    DECOMPOSITION_SESSION_DUPLICATE_SUBMIT,
                    parent_task_id=self._parent_task_id,
                    previous_subtask_count=len(self._plan.subtasks),
                    new_subtask_count=len(plan.subtasks),
                )
            self._plan = plan


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
            # Logged as well as returned: the rejection the agent reads is one
            # tool result, and the question an expensive session raises later
            # is whether it was handed the same one repeatedly, which only the
            # log can answer.
            logger.info(
                DECOMPOSITION_SESSION_PLAN_REJECTED,
                parent_task_id=self._parent_task_id,
                error_type=type(exc).__name__,
                error=safe_error_description(exc),
            )
            return ToolExecutionResult(
                content=(
                    f"Plan rejected: {safe_error_description(exc)}. "
                    "Fix the issue and call submit_decomposition_plan again."
                ),
                is_error=True,
            )
        await self._capture.set(plan)
        return ToolExecutionResult(
            content=(
                f"Plan accepted with {len(plan.subtasks)} subtasks. You may stop now."
            ),
        )


__all__ = ["PlanCapture", "SubmitDecompositionPlanTool"]
