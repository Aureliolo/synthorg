"""Plan parsing utilities for the Plan-and-Execute loop.

Provides functions to extract an ``ExecutionPlan`` from LLM response
content.  Tries JSON extraction first (with markdown code fence
stripping), then falls back to structured text parsing.
"""

import json
import re
from typing import TYPE_CHECKING

from synthorg.observability import get_logger
from synthorg.observability.events.execution import (
    EXECUTION_PLAN_PARSE_ERROR,
)

from .plan_models import ExecutionPlan, PlanStep

if TYPE_CHECKING:
    from synthorg.providers.models import CompletionResponse

logger = get_logger(__name__)

_PLANNING_PROMPT = """\
You are a planning agent. Analyze the task and create a step-by-step \
execution plan. Return your plan as a JSON object with this exact schema:

```json
{
  "steps": [
    {
      "step_number": 1,
      "description": "What to do in this step",
      "expected_outcome": "What should result from this step"
    }
  ]
}
```

Each step should be concrete, actionable, and independently verifiable. \
Return ONLY the JSON object, no other text."""

_REPLAN_JSON_EXAMPLE = """\
```json
{
  "steps": [
    {
      "step_number": 1,
      "description": "What to do in this step",
      "expected_outcome": "What should result from this step"
    }
  ]
}
```"""


def parse_plan(
    response: CompletionResponse,
    execution_id: str,
    task_summary: str,
    *,
    revision_number: int = 0,
) -> ExecutionPlan | None:
    """Parse an ExecutionPlan from LLM response content.

    Tries JSON extraction first (with markdown code fence stripping),
    then falls back to structured text parsing.

    Args:
        response: LLM completion response.
        execution_id: Execution ID for logging.
        task_summary: Brief summary of the task being planned.
        revision_number: Plan revision counter (0 = original).

    Returns:
        Parsed ``ExecutionPlan``, or ``None`` on failure.
    """
    content = response.content or ""
    if not content.strip():
        logger.warning(
            EXECUTION_PLAN_PARSE_ERROR,
            execution_id=execution_id,
            reason="empty LLM response content",
        )
        return None

    plan = _parse_json_plan(content, task_summary, revision_number)
    if plan is not None:
        return plan

    plan = _parse_text_plan(content, task_summary, revision_number)
    if plan is not None:
        return plan

    logger.warning(
        EXECUTION_PLAN_PARSE_ERROR,
        execution_id=execution_id,
        content_length=len(content),
    )
    return None


def _parse_json_plan(
    content: str,
    task_summary: str,
    revision_number: int,
) -> ExecutionPlan | None:
    """Try to extract a JSON plan from the content."""
    json_str = content.strip()
    fence_match = re.search(
        r"```(?:json)?\s*\n?(.*?)```",
        json_str,
        re.DOTALL,
    )
    if fence_match:
        json_str = fence_match.group(1).strip()

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as exc:
        logger.debug(
            EXECUTION_PLAN_PARSE_ERROR,
            parser="json",
            error=str(exc),
        )
        return None

    return _data_to_plan(data, task_summary, revision_number)


def _parse_text_plan(
    content: str,
    task_summary: str,
    revision_number: int,
) -> ExecutionPlan | None:
    """Fallback: extract steps from numbered text lines."""
    step_pattern = re.compile(
        r"(?:^|\n)\s*(\d+)\.\s+(.+?)(?=\n\s*\d+\.|\Z)",
        re.DOTALL,
    )
    matches = step_pattern.findall(content)
    if not matches:
        logger.debug(
            EXECUTION_PLAN_PARSE_ERROR,
            parser="text_fallback",
            reason="no numbered steps found",
        )
        return None

    steps: list[PlanStep] = []
    for _, desc in matches:
        desc_clean = desc.strip()
        if not desc_clean:
            continue
        steps.append(
            PlanStep(
                step_number=len(steps) + 1,
                description=desc_clean,
                expected_outcome=desc_clean,
            )
        )

    if not steps:
        logger.debug(
            EXECUTION_PLAN_PARSE_ERROR,
            parser="text_fallback",
            reason="all descriptions empty after stripping",
        )
        return None

    try:
        return ExecutionPlan(
            steps=tuple(steps),
            revision_number=revision_number,
            original_task_summary=task_summary,
        )
    except ValueError as exc:
        logger.debug(
            EXECUTION_PLAN_PARSE_ERROR,
            parser="text_fallback",
            error=str(exc),
        )
        return None


def _data_to_plan(
    data: object,
    task_summary: str,
    revision_number: int,
) -> ExecutionPlan | None:
    """Convert parsed JSON data to an ExecutionPlan."""
    if not isinstance(data, dict):
        logger.debug(
            EXECUTION_PLAN_PARSE_ERROR,
            parser="json_data",
            reason="top-level value is not a dict",
            data_type=type(data).__name__,
        )
        return None

    raw_steps = data.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        logger.debug(
            EXECUTION_PLAN_PARSE_ERROR,
            parser="json_data",
            reason="missing or empty 'steps' list",
        )
        return None

    steps: list[PlanStep] = []
    for i, raw_step in enumerate(raw_steps, start=1):
        if not isinstance(raw_step, dict):
            logger.debug(
                EXECUTION_PLAN_PARSE_ERROR,
                parser="json_data",
                reason=f"step {i} is not a dict",
            )
            return None
        desc = raw_step.get("description", "")
        outcome = raw_step.get("expected_outcome", desc)
        if not desc:
            logger.debug(
                EXECUTION_PLAN_PARSE_ERROR,
                parser="json_data",
                reason=f"step {i} has no description",
            )
            return None
        steps.append(
            PlanStep(
                step_number=i,
                description=str(desc),
                expected_outcome=str(outcome),
            )
        )

    try:
        return ExecutionPlan(
            steps=tuple(steps),
            revision_number=revision_number,
            original_task_summary=task_summary,
        )
    except ValueError as exc:
        logger.debug(
            EXECUTION_PLAN_PARSE_ERROR,
            parser="json_data",
            error=str(exc),
        )
        return None
