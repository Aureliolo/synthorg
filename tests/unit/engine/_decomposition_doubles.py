# module-kind: tests
"""What a decomposition prompt is built from and what a planner answers with.

Shared by the prompt-building tests and the response-parsing tests, which are
different questions about the same wire: one asks what the planner is told, the
other what happens when it replies. Held here so the two files cannot drift
apart on the shape of a plan they both build.
"""

from typing import Final, cast

from pydantic import JsonValue

from synthorg.core.completion_enums import FinishReason
from synthorg.core.task import AcceptanceCriterion, Task
from synthorg.core.task_enums import Priority, TaskType
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.context import DecompositionContext
from synthorg.providers.models import CompletionResponse, TokenUsage, ToolCall
from tests._shared import as_uuid

#: A roster in the shape the shipped template staffs, so "Backend Engineer"
#: is a near-miss a real decomposition can produce against real staffing
#: rather than an invention of this test.
ROSTER: Final[tuple[NotBlankStr, ...]] = (
    NotBlankStr("Backend Developer"),
    NotBlankStr("Frontend Developer"),
    NotBlankStr("QA Engineer"),
)


def make_task(
    task_id: str = "task-llm-1",
    *,
    title: str = "Implement auth module",
    description: str = "Build JWT authentication for the API.",
    criteria: tuple[AcceptanceCriterion, ...] = (),
) -> Task:
    """Create a minimal task for prompt tests.

    Returns:
        The task.
    """
    return Task(
        id=as_uuid(task_id),
        title=title,
        description=description,
        type=TaskType.DEVELOPMENT,
        priority=Priority.HIGH,
        project="proj-1",
        created_by="creator",
        acceptance_criteria=criteria,
    )


def make_context(
    max_subtasks: int = 10,
    max_depth: int = 3,
    current_depth: int = 0,
    objective_criteria: tuple[NotBlankStr, ...] = (),
) -> DecompositionContext:
    """Create a decomposition context.

    Returns:
        The context.
    """
    return DecompositionContext(
        max_subtasks=max_subtasks,
        max_depth=max_depth,
        current_depth=current_depth,
        objective_criteria=objective_criteria,
    )


def make_tool_call_response(
    arguments: dict[str, object],
    *,
    tool_name: str = "submit_decomposition_plan",
) -> CompletionResponse:
    """Create a CompletionResponse with a single tool call.

    Returns:
        The response.
    """
    return CompletionResponse(
        tool_calls=(
            ToolCall(
                id="tc-1",
                name=tool_name,
                arguments=cast("dict[str, JsonValue]", arguments),
            ),
        ),
        finish_reason=FinishReason.TOOL_USE,
        usage=TokenUsage(input_tokens=100, output_tokens=50, cost=0.01),
        model="test-model-001",
    )


def make_content_response(content: str) -> CompletionResponse:
    """Create a CompletionResponse with text content only.

    Returns:
        The response.
    """
    return CompletionResponse(
        content=content,
        finish_reason=FinishReason.STOP,
        usage=TokenUsage(input_tokens=100, output_tokens=50, cost=0.01),
        model="test-model-001",
    )


def valid_plan_args(
    *,
    subtask_count: int = 2,
    task_structure: str = "sequential",
    coordination_topology: str = "auto",
    required_role: str = "Backend Engineer",
) -> dict[str, object]:
    """Build valid tool call arguments for a decomposition plan.

    Returns:
        The arguments a planner would send.
    """
    subtasks = [
        {
            "id": f"sub-{i}",
            "title": f"Subtask {i}",
            "description": f"Do step {i}",
            "dependencies": [] if i == 0 else [f"sub-{i - 1}"],
            "estimated_complexity": "medium",
            "required_skills": ["python"],
            "required_role": required_role,
            "expected_artifacts": [f"src/step_{i}.py"],
            "acceptance_criteria": [f"step {i} verified"],
        }
        for i in range(subtask_count)
    ]
    return {
        "subtasks": subtasks,
        "task_structure": task_structure,
        "coordination_topology": coordination_topology,
    }


__all__ = [
    "ROSTER",
    "make_content_response",
    "make_context",
    "make_task",
    "make_tool_call_response",
    "valid_plan_args",
]
