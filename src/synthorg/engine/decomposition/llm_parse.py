"""Response parsing for LLM-based decomposition.

Pure functions that turn an LLM completion (tool call or JSON content) into a
validated :class:`DecompositionPlan`. The prompt-building side lives in
:mod:`synthorg.engine.decomposition.llm_prompt`; both share the canonical
``submit_decomposition_plan`` tool name.
"""

import json
import re
from enum import Enum
from typing import Final
from uuid import uuid4

from pydantic import JsonValue

from synthorg.core.task_enums import Complexity, CoordinationTopology, TaskStructure
from synthorg.engine.decomposition.llm_prompt import TOOL_NAME
from synthorg.engine.decomposition.models import (
    DecompositionPlan,
    SubtaskDefinition,
)
from synthorg.engine.errors import DecompositionError
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.decomposition import (
    DECOMPOSITION_LLM_PARSE_ERROR,
)
from synthorg.providers.models import CompletionResponse

logger = get_logger(__name__)

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


def _enum_or_default[E: Enum](
    raw_value: object,
    mapping: dict[str, E],
    default: E,
    *,
    field: str,
) -> E:
    """Resolve a lowercased enum value from *mapping*, defaulting on a miss.

    Args:
        raw_value: The raw value from the LLM response (coerced to a lowercased
            string for the lookup).
        mapping: The value-to-member map for the target enum.
        default: The member returned when *raw_value* is unrecognised.
        field: The field name, for the warning context.

    Returns:
        The mapped enum member, or *default* (with a logged warning) on a miss.
    """
    resolved = mapping.get(str(raw_value).lower())
    if resolved is not None:
        return resolved
    logger.warning(
        DECOMPOSITION_LLM_PARSE_ERROR,
        raw_value=raw_value,
        default=default.value,
        error=f"Unknown {field}: {raw_value!r}, defaulting to {default.value}",
    )
    return default


def _parse_subtask(raw: dict[str, JsonValue]) -> SubtaskDefinition:
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

    complexity = _enum_or_default(
        raw.get("estimated_complexity", "medium"),
        _COMPLEXITY_MAP,
        Complexity.MEDIUM,
        field="complexity",
    )
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
    artifacts = _string_array(raw, "expected_artifacts")
    acceptance = _string_array(raw, "acceptance_criteria")
    return SubtaskDefinition.model_validate(
        {
            "id": raw["id"],
            "title": raw["title"],
            "description": raw["description"],
            "dependencies": tuple(deps),
            "estimated_complexity": complexity,
            "required_skills": tuple(skills),
            "required_role": raw.get("required_role"),
            "expected_artifacts": artifacts,
            "acceptance_criteria": acceptance,
        }
    )


def _string_array(raw: dict[str, JsonValue], field: str) -> tuple[str, ...]:
    """Coerce an optional LLM string-array field into a tuple.

    Args:
        raw: The raw subtask dict from tool call arguments.
        field: The array-valued field name to read.

    Returns:
        A tuple of the array's entries (empty when the field is absent).

    Raises:
        DecompositionError: When the field is present and truthy but not a
            list; a falsy non-list value (``0`` / ``False`` / ``""``) is
            treated as absent.
    """
    values = raw.get(field) or []
    if not isinstance(values, list):
        msg = f"Subtask field {field!r} must be an array"
        logger.warning(DECOMPOSITION_LLM_PARSE_ERROR, error=msg)
        raise DecompositionError(msg)
    return tuple(str(v) for v in values)


def _remap_subtask_ids(
    parsed: tuple[SubtaskDefinition, ...],
) -> tuple[SubtaskDefinition, ...]:
    """Remap model-assigned subtask ids to fresh UUIDs, validating the DAG.

    The model assigns its own subtask ids (e.g. ``"subtask-1"``) purely to
    express the dependency edges; this remaps each to a globally unique UUID
    while preserving those edges. Duplicate ids (which would collapse distinct
    subtasks onto one identifier) and dependencies naming a subtask the model
    never defined (a hallucination) are both rejected with a correctable
    :class:`DecompositionError` so the agent-session strategy can resubmit,
    rather than passing a dangling id through to fail opaquely at DAG
    validation.

    Args:
        parsed: The parsed subtasks carrying the model-assigned ids.

    Returns:
        The subtasks with UUID ids and remapped dependency edges.

    Raises:
        DecompositionError: On a duplicate id or an unknown dependency id.
    """
    id_map: dict[str, str] = {}
    for sub in parsed:
        if sub.id in id_map:
            msg = f"Duplicate subtask id: {sub.id!r}"
            logger.warning(DECOMPOSITION_LLM_PARSE_ERROR, error=msg)
            raise DecompositionError(msg)
        id_map[sub.id] = str(uuid4())
    for sub in parsed:
        for dep in sub.dependencies:
            if dep not in id_map:
                msg = f"Subtask {sub.id!r} depends on unknown subtask {dep!r}"
                logger.warning(DECOMPOSITION_LLM_PARSE_ERROR, error=msg)
                raise DecompositionError(msg)
    return tuple(
        sub.model_copy(
            update={
                "id": id_map[sub.id],
                "dependencies": tuple(id_map[dep] for dep in sub.dependencies),
            }
        )
        for sub in parsed
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

    parsed = tuple(_parse_subtask(s) for s in raw_subtasks if isinstance(s, dict))
    subtasks = _remap_subtask_ids(parsed)

    structure = _enum_or_default(
        args.get("task_structure", "sequential"),
        _TASK_STRUCTURE_MAP,
        TaskStructure.SEQUENTIAL,
        field="task_structure",
    )
    topology = _enum_or_default(
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
