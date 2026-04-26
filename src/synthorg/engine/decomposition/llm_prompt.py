"""Prompt building and response parsing for LLM-based decomposition.

Pure functions that construct messages, tool definitions, and parse
LLM responses into ``DecompositionPlan`` objects.
"""

import json
import re
from typing import TYPE_CHECKING, Any, Final

from synthorg.core.enums import (
    Complexity,
    CoordinationTopology,
    TaskStructure,
)
from synthorg.engine.decomposition.models import (
    DecompositionPlan,
    SubtaskDefinition,
)
from synthorg.engine.errors import DecompositionError
from synthorg.engine.prompt_safety import (
    TAG_TASK_DATA,
    untrusted_content_directive,
    wrap_untrusted,
)
from synthorg.observability import get_logger
from synthorg.observability.events.decomposition import (
    DECOMPOSITION_LLM_PARSE_ERROR,
)
from synthorg.providers.enums import MessageRole
from synthorg.providers.models import (
    ChatMessage,
    CompletionResponse,
    ToolDefinition,
)

if TYPE_CHECKING:
    from synthorg.core.task import Task
    from synthorg.engine.decomposition.models import (
        DecompositionContext,
    )

logger = get_logger(__name__)

_TOOL_NAME = "submit_decomposition_plan"

_COMPLEXITY_MAP: Final[dict[str, Complexity]] = {c.value: c for c in Complexity}

_TASK_STRUCTURE_MAP: Final[dict[str, TaskStructure]] = {
    s.value: s for s in TaskStructure
}

_TOPOLOGY_MAP: Final[dict[str, CoordinationTopology]] = {
    t.value: t for t in CoordinationTopology
}

_MARKDOWN_FENCE_RE = re.compile(
    r"```(?:json)?\s*\n(.*?)\n\s*```",
    re.DOTALL,
)


def build_decomposition_tool() -> ToolDefinition:
    """Build the ``submit_decomposition_plan`` tool definition.

    Returns:
        A ``ToolDefinition`` with a JSON Schema describing the plan
        structure, including subtask definitions with dependencies
        and complexity metadata.
    """
    subtask_schema: dict[str, Any] = {
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
        },
        "required": ["id", "title", "description"],
    }
    schema: dict[str, Any] = {
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
        name=_TOOL_NAME,
        description=(
            "Submit a task decomposition plan with subtasks, "
            "their dependencies, and coordination metadata."
        ),
        parameters_schema=schema,
    )


def build_system_message() -> ChatMessage:
    """Build the system prompt for decomposition.

    The hand-rolled "treat <task-data> as untrusted" warning is replaced
    by the canonical :func:`untrusted_content_directive` so the SEC-1
    fingerprint test catches silent drift in the wording.

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


def _parse_subtask(raw: dict[str, Any]) -> SubtaskDefinition:
    """Convert a raw subtask dict into a ``SubtaskDefinition``.

    Args:
        raw: Dict from LLM tool call arguments.

    Returns:
        A validated ``SubtaskDefinition``.

    Raises:
        DecompositionError: If required fields are missing.
    """
    for field in ("id", "title", "description"):
        if field not in raw:
            msg = (
                f"Subtask missing required field '{field}'. "
                f"Available keys: {sorted(raw.keys())}"
            )
            logger.warning(
                DECOMPOSITION_LLM_PARSE_ERROR,
                error=msg,
            )
            raise DecompositionError(msg)

    complexity_str = raw.get("estimated_complexity", "medium")
    complexity = _COMPLEXITY_MAP.get(str(complexity_str).lower())
    if complexity is None:
        logger.warning(
            DECOMPOSITION_LLM_PARSE_ERROR,
            raw_value=complexity_str,
            default="medium",
            error=f"Unknown complexity value: {complexity_str!r}, defaulting to medium",
        )
        complexity = Complexity.MEDIUM
    deps = raw.get("dependencies") or []
    if not isinstance(deps, list):
        msg = "Subtask field 'dependencies' must be an array"
        logger.warning(
            DECOMPOSITION_LLM_PARSE_ERROR,
            error=msg,
        )
        raise DecompositionError(msg)
    skills = raw.get("required_skills") or []
    if not isinstance(skills, list):
        msg = "Subtask field 'required_skills' must be an array"
        logger.warning(
            DECOMPOSITION_LLM_PARSE_ERROR,
            error=msg,
        )
        raise DecompositionError(msg)
    return SubtaskDefinition(
        id=raw["id"],
        title=raw["title"],
        description=raw["description"],
        dependencies=tuple(deps),
        estimated_complexity=complexity,
        required_skills=tuple(skills),
        required_role=raw.get("required_role"),
    )


def _args_to_plan(
    args: dict[str, Any],
    parent_task_id: str,
) -> DecompositionPlan:
    """Convert parsed arguments dict into a ``DecompositionPlan``.

    Args:
        args: Parsed tool call arguments or JSON content.
        parent_task_id: ID of the parent task.

    Returns:
        A validated ``DecompositionPlan``.

    Raises:
        DecompositionError: If the arguments are invalid.
    """
    raw_subtasks = args.get("subtasks")
    if not isinstance(raw_subtasks, list):
        msg = "Field 'subtasks' must be an array"
        logger.warning(
            DECOMPOSITION_LLM_PARSE_ERROR,
            error=msg,
        )
        raise DecompositionError(msg)
    if not raw_subtasks:
        msg = "No subtasks found in response"
        logger.warning(
            DECOMPOSITION_LLM_PARSE_ERROR,
            error=msg,
        )
        raise DecompositionError(msg)
    if any(not isinstance(s, dict) for s in raw_subtasks):
        msg = "Each subtask must be an object"
        logger.warning(
            DECOMPOSITION_LLM_PARSE_ERROR,
            error=msg,
        )
        raise DecompositionError(msg)

    subtasks = tuple(_parse_subtask(s) for s in raw_subtasks)

    structure_str = args.get("task_structure", "sequential")
    structure = _TASK_STRUCTURE_MAP.get(str(structure_str).lower())
    if structure is None:
        logger.warning(
            DECOMPOSITION_LLM_PARSE_ERROR,
            raw_value=structure_str,
            default="sequential",
            error=f"Unknown task_structure: {structure_str!r}, using sequential",
        )
        structure = TaskStructure.SEQUENTIAL

    topology_str = args.get("coordination_topology", "auto")
    topology = _TOPOLOGY_MAP.get(str(topology_str).lower())
    if topology is None:
        logger.warning(
            DECOMPOSITION_LLM_PARSE_ERROR,
            raw_value=topology_str,
            default="auto",
            error=f"Unknown topology: {topology_str!r}, defaulting to auto",
        )
        topology = CoordinationTopology.AUTO

    return DecompositionPlan(
        parent_task_id=parent_task_id,
        subtasks=subtasks,
        task_structure=structure,
        coordination_topology=topology,
    )


def parse_tool_call_response(
    response: CompletionResponse,
    parent_task_id: str,
) -> DecompositionPlan:
    """Extract a plan from a tool call response.

    Looks for a tool call named ``submit_decomposition_plan``
    and parses its arguments into a ``DecompositionPlan``.

    Args:
        response: The LLM completion response.
        parent_task_id: ID of the parent task.

    Returns:
        A validated ``DecompositionPlan``.

    Raises:
        DecompositionError: If no matching tool call is found
            or arguments are invalid.
    """
    for tc in response.tool_calls:
        if tc.name == _TOOL_NAME:
            try:
                return _args_to_plan(tc.arguments, parent_task_id)
            except DecompositionError as exc:
                # Re-raise without wrapping to preserve the original error
                logger.warning(
                    DECOMPOSITION_LLM_PARSE_ERROR,
                    error=str(exc),
                    parent_task_id=parent_task_id,
                )
                raise
            except Exception as exc:
                logger.warning(
                    DECOMPOSITION_LLM_PARSE_ERROR,
                    error=str(exc),
                    exc_type=type(exc).__name__,
                )
                msg = f"Failed to parse tool call arguments: {exc}"
                raise DecompositionError(msg) from exc

    msg = "No tool call for submit_decomposition_plan found"
    logger.warning(
        DECOMPOSITION_LLM_PARSE_ERROR,
        error=msg,
        parent_task_id=parent_task_id,
    )
    raise DecompositionError(msg)


def parse_content_response(
    response: CompletionResponse,
    parent_task_id: str,
) -> DecompositionPlan:
    """Extract a plan from content text.

    Attempts to parse JSON directly, or from a markdown
    code fence.

    Args:
        response: The LLM completion response.
        parent_task_id: ID of the parent task.

    Returns:
        A validated ``DecompositionPlan``.

    Raises:
        DecompositionError: If content is missing or cannot
            be parsed.
    """
    if response.content is None:
        msg = "Response has no content to parse"
        logger.warning(
            DECOMPOSITION_LLM_PARSE_ERROR,
            error=msg,
            parent_task_id=parent_task_id,
        )
        raise DecompositionError(msg)

    text = response.content.strip()

    match = _MARKDOWN_FENCE_RE.search(text)
    if match:
        text = match.group(1).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        msg = f"Failed to parse JSON from content: {exc}"
        logger.warning(
            DECOMPOSITION_LLM_PARSE_ERROR,
            error=msg,
            parent_task_id=parent_task_id,
        )
        raise DecompositionError(msg) from exc

    try:
        return _args_to_plan(data, parent_task_id)
    except DecompositionError as exc:
        # Re-raise without wrapping to preserve the original error
        logger.warning(
            DECOMPOSITION_LLM_PARSE_ERROR,
            error=str(exc),
            parent_task_id=parent_task_id,
        )
        raise
    except Exception as exc:
        logger.warning(
            DECOMPOSITION_LLM_PARSE_ERROR,
            error=str(exc),
            exc_type=type(exc).__name__,
        )
        msg = f"Failed to parse plan from content JSON: {exc}"
        raise DecompositionError(msg) from exc
