"""Response parsing for LLM-based decomposition.

Pure functions that turn an LLM completion (tool call or JSON content) into a
validated :class:`DecompositionPlan`. The prompt-building side lives in
:mod:`synthorg.engine.decomposition.llm_prompt`; both share the canonical
``submit_decomposition_plan`` tool name. The per-subtask half lives in
:mod:`synthorg.engine.decomposition.llm_parse_subtask`.
"""

import json
import re
from typing import Final

from pydantic import JsonValue

from synthorg.core.task_enums import CoordinationTopology, TaskStructure
from synthorg.engine.decomposition.llm_parse_subtask import (
    enum_or_default,
    parse_subtask,
    remap_subtask_ids,
    string_array,
)
from synthorg.engine.decomposition.llm_prompt import TOOL_NAME
from synthorg.engine.decomposition.models import DecompositionPlan
from synthorg.engine.errors import DecompositionError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.decomposition import (
    DECOMPOSITION_LLM_PARSE_ERROR,
)
from synthorg.providers.models import CompletionResponse

logger = get_logger(__name__)

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


def _structure_or_none(raw_value: object) -> TaskStructure | None:
    """Resolve a declared task structure, or ``None`` when none was declared.

    Returns:
        The mapped member, or ``None`` when the planner omitted the field.
    """
    if raw_value is None:
        return None
    return enum_or_default(
        raw_value, _TASK_STRUCTURE_MAP, TaskStructure.SEQUENTIAL, field="task_structure"
    )


def _args_to_plan(
    args: dict[str, JsonValue],
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
        logger.warning(DECOMPOSITION_LLM_PARSE_ERROR, error=msg)
        raise DecompositionError(msg)
    if not raw_subtasks:
        msg = "No subtasks found in response"
        logger.warning(DECOMPOSITION_LLM_PARSE_ERROR, error=msg)
        raise DecompositionError(msg)
    if any(not isinstance(s, dict) for s in raw_subtasks):
        msg = "Each subtask must be an object"
        logger.warning(DECOMPOSITION_LLM_PARSE_ERROR, error=msg)
        raise DecompositionError(msg)

    parsed = tuple(parse_subtask(s) for s in raw_subtasks if isinstance(s, dict))
    subtasks = remap_subtask_ids(parsed)

    # Absent stays None: the classifier fallback tells "declared nothing"
    # apart from an explicit "sequential", and defaulting here erases that.
    structure = _structure_or_none(args.get("task_structure"))
    topology = enum_or_default(
        args.get("coordination_topology", "auto"),
        _TOPOLOGY_MAP,
        CoordinationTopology.AUTO,
        field="coordination_topology",
    )

    return DecompositionPlan(
        parent_task_id=parent_task_id,
        subtasks=subtasks,
        task_structure=structure,
        coordination_topology=topology,
        open_questions=string_array(args, "open_questions"),
        assumptions=string_array(args, "assumptions"),
    )


def args_to_decomposition_plan(
    args: dict[str, JsonValue],
    parent_task_id: str,
) -> DecompositionPlan:
    """Parse raw submit-plan arguments into a validated ``DecompositionPlan``.

    The thin public wrapper the agent-session strategy's terminal submit tool
    calls directly; the single-shot strategy's ``parse_tool_call_response`` /
    ``parse_content_response`` reach the same ``_args_to_plan`` logic
    internally. It guarantees every failure surfaces as a ``DecompositionError``
    (never a raw ``pydantic.ValidationError`` from a model validator such as
    the self-dependency or ``NotBlankStr`` checks), so a malformed submission
    reaches the caller as a single, correctable error type.

    Args:
        args: The submit-plan tool-call arguments (or equivalent JSON).
        parent_task_id: ID of the parent task the plan decomposes.

    Returns:
        A validated ``DecompositionPlan``.

    Raises:
        DecompositionError: If the arguments are invalid.
    """
    try:
        return _args_to_plan(args, parent_task_id)
    except DecompositionError:
        raise
    except Exception as exc:
        error_desc = safe_error_description(exc)
        logger.warning(
            DECOMPOSITION_LLM_PARSE_ERROR,
            error_type=type(exc).__name__,
            error=error_desc,
            parent_task_id=parent_task_id,
        )
        msg = f"Failed to parse plan arguments: {error_desc}"
        raise DecompositionError(msg) from exc


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
        if tc.name == TOOL_NAME:
            try:
                return _args_to_plan(tc.arguments, parent_task_id)
            except DecompositionError as exc:
                # Re-raise without wrapping to preserve the original error
                logger.warning(
                    DECOMPOSITION_LLM_PARSE_ERROR,
                    error_type=type(exc).__name__,
                    error=safe_error_description(exc),
                    parent_task_id=parent_task_id,
                )
                raise
            except Exception as exc:
                error_desc = safe_error_description(exc)
                logger.warning(
                    DECOMPOSITION_LLM_PARSE_ERROR,
                    error_type=type(exc).__name__,
                    error=error_desc,
                    parent_task_id=parent_task_id,
                )
                msg = f"Failed to parse tool call arguments: {error_desc}"
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
        msg = f"Failed to parse JSON from content: {safe_error_description(exc)}"
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
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
            parent_task_id=parent_task_id,
        )
        raise
    except Exception as exc:
        error_desc = safe_error_description(exc)
        logger.warning(
            DECOMPOSITION_LLM_PARSE_ERROR,
            error_type=type(exc).__name__,
            error=error_desc,
            parent_task_id=parent_task_id,
        )
        msg = f"Failed to parse plan from content JSON: {error_desc}"
        raise DecompositionError(msg) from exc
