"""Prompt building for LLM-based decomposition.

Pure functions that construct the system/user messages and the
``submit_decomposition_plan`` tool definition. Response parsing lives in
:mod:`synthorg.engine.decomposition.llm_parse`; both share ``TOOL_NAME``.
"""

from pydantic import JsonValue

from synthorg.core.task import Task
from synthorg.core.task_enums import Complexity, CoordinationTopology, TaskStructure
from synthorg.engine.decomposition.models import DecompositionContext
from synthorg.engine.prompt_safety import (
    TAG_TASK_DATA,
    untrusted_content_directive,
    wrap_untrusted,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import (
    ChatMessage,
    ToolDefinition,
)

TOOL_NAME = "submit_decomposition_plan"


def build_decomposition_tool() -> ToolDefinition:
    """Build the ``submit_decomposition_plan`` tool definition.

    Returns:
        A ``ToolDefinition`` with a JSON Schema describing the plan
        structure, including subtask definitions with dependencies
        and complexity metadata.
    """
    subtask_schema: dict[str, JsonValue] = {
        "type": "object",
        "properties": {
            "id": {
                "type": "string",
                "description": "Unique subtask identifier",
            },
            "title": {
                "type": "string",
                "description": "Short subtask title",
            },
            "description": {
                "type": "string",
                "description": "Detailed subtask description",
            },
            "dependencies": {
                "type": "array",
                "items": {"type": "string"},
                "description": "IDs of subtasks this depends on",
            },
            "estimated_complexity": {
                "type": "string",
                "enum": [c.value for c in Complexity],
                "description": "Complexity estimate",
            },
            "required_skills": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Skills needed for this subtask",
            },
            "required_role": {
                "type": ["string", "null"],
                "description": "Optional role for routing",
            },
            "expected_artifacts": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Concrete deliverables this subtask must produce "
                    "(file paths, docs, or test suites). Non-empty so the "
                    "fail-loud zero-artifact guard engages when it runs."
                ),
            },
            "acceptance_criteria": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Verifiable criteria that define done for this subtask",
            },
        },
        "required": [
            "id",
            "title",
            "description",
            "expected_artifacts",
            "acceptance_criteria",
        ],
    }
    schema: dict[str, JsonValue] = {
        "type": "object",
        "properties": {
            "subtasks": {
                "type": "array",
                "items": subtask_schema,
                "description": "Ordered subtask definitions",
            },
            "task_structure": {
                "type": "string",
                "enum": [s.value for s in TaskStructure],
                "description": "Overall task structure",
            },
            "coordination_topology": {
                "type": "string",
                "enum": [t.value for t in CoordinationTopology],
                "description": "Coordination topology",
            },
        },
        "required": ["subtasks"],
    }
    return ToolDefinition(
        name=TOOL_NAME,
        description=(
            "Submit a task decomposition plan with subtasks, "
            "their dependencies, and coordination metadata."
        ),
        parameters_schema=schema,
    )


def build_system_message() -> ChatMessage:
    """Build the system prompt for decomposition.

    The hand-rolled "treat <task-data> as untrusted" warning is
    replaced by the canonical :func:`untrusted_content_directive` so
    the prompt-fingerprint test catches silent drift in the wording.

    Returns:
        A ``ChatMessage`` with ``MessageRole.SYSTEM``.
    """
    content = (
        "You are a task decomposition expert. Your job is to "
        "break down a complex task into smaller, well-defined "
        "subtasks.\n\n"
        "Guidelines:\n"
        "- Each subtask must have a unique ID, clear title, "
        "and detailed description.\n"
        "- Specify dependencies between subtasks where "
        "needed.\n"
        "- Estimate complexity for each subtask "
        "(simple, medium, complex, epic).\n"
        "- For each subtask, list the concrete deliverables it must "
        "produce as expected_artifacts (file paths, docs, or test "
        "suites) and the verifiable acceptance_criteria that define "
        "when it is done. Never leave these empty.\n"
        "- Classify the overall task structure "
        "(sequential, parallel, mixed).\n"
        "- Choose an appropriate coordination topology.\n"
        "- Use the submit_decomposition_plan tool to provide "
        "your answer.\n"
        "- If a tool call is not possible, respond with a "
        "JSON object in the same schema.\n\n"
        + untrusted_content_directive((TAG_TASK_DATA,))
    )
    return ChatMessage(role=MessageRole.SYSTEM, content=content)


def build_task_message(
    task: Task,
    context: DecompositionContext,
) -> ChatMessage:
    """Build the user message with task details and constraints.

    Task fields (title, description, acceptance criteria) originate from
    public API payloads and must be treated as attacker-controllable.
    They are routed through :func:`wrap_untrusted` so an attacker who
    embeds the literal closing fence cannot break out -- mirrors
    :func:`synthorg.engine.prompt_validation.format_task_instruction`.
    Constraints sit outside the fence: numeric system-controlled values
    carry no breakout vector.

    Args:
        task: The parent task to decompose.
        context: Decomposition constraints.

    Returns:
        A ``ChatMessage`` with ``MessageRole.USER``.
    """
    inner: list[str] = [
        f"Title: {task.title}",
        f"Description: {task.description}",
    ]
    if task.acceptance_criteria:
        inner.append("Acceptance Criteria:")
        inner.extend(f"  - {c.description}" for c in task.acceptance_criteria)

    parts = [
        wrap_untrusted(TAG_TASK_DATA, "\n".join(inner)),
        "",
        "Constraints:",
        f"  max_subtasks: {context.max_subtasks}",
        f"  current_depth: {context.current_depth}",
        f"  max_depth: {context.max_depth}",
    ]
    content = "\n".join(parts)
    return ChatMessage(role=MessageRole.USER, content=content)


def build_retry_message(error: str) -> ChatMessage:
    """Build a retry message with the prior error.

    Args:
        error: Description of the parsing/validation error.

    Returns:
        A ``ChatMessage`` with ``MessageRole.USER``.
    """
    content = (
        "Your previous response could not be parsed. "
        f"Error: {error}\n\n"
        "Please try again using the "
        "submit_decomposition_plan tool with corrected "
        "arguments."
    )
    return ChatMessage(role=MessageRole.USER, content=content)
