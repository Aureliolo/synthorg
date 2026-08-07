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

from synthorg.core.plan_validation import describe_unroutable_role
from synthorg.core.task_enums import CoordinationTopology, TaskStructure
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition.llm_parse_subtask import (
    enum_or_default,
    parse_subtask,
    remap_subtask_ids,
    string_array,
)
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


def _declared_structure(args: dict[str, JsonValue]) -> TaskStructure:
    """Resolve the declared task structure, or ``AUTO`` when none was declared.

    Unlike the other enum fields, an unrecognised value raises rather than
    degrading to a default. The tool schema constrains this field to the
    enum, so a value outside it is the planner failing to declare rather
    than declaring awkwardly, and every remaining member is a real answer
    that would bind the coordination topology. Guessing one would be the
    silent substitution ``AUTO`` exists to make impossible.

    Only an absent key means "undeclared". An explicit ``null`` is a value
    the schema does not admit, so it is rejected like any other unknown one
    rather than read as the omission it is not.

    Args:
        args: The submit-plan arguments the ``task_structure`` key is read
            from, so presence is distinguishable from an explicit ``null``.

    Returns:
        The mapped member, or ``AUTO`` when the planner omitted the field.

    Raises:
        DecompositionError: When the field is present but names no member.
    """
    if "task_structure" not in args:
        return TaskStructure.AUTO
    raw_value = args["task_structure"]
    resolved = (
        None if raw_value is None else _TASK_STRUCTURE_MAP.get(str(raw_value).lower())
    )
    if resolved is None:
        msg = (
            f"Unknown task_structure: {raw_value!r}; expected one of "
            f"{sorted(_TASK_STRUCTURE_MAP)}"
        )
        logger.warning(DECOMPOSITION_LLM_PARSE_ERROR, error=msg)
        raise DecompositionError(msg)
    return resolved


def _validate_roles(
    subtasks: tuple[SubtaskDefinition, ...],
    available_roles: tuple[NotBlankStr, ...],
) -> None:
    """Reject an owner the org does not staff.

    Sits with the kind/artifact invariant rather than at dispatch, because a
    correctable :class:`DecompositionError` lets the planning session resubmit
    inside the same session, while an unroutable owner discovered at dispatch
    has already been approved by an operator who was told nothing was wrong.

    Args:
        subtasks: The parsed subtasks.
        available_roles: The roles the org staffs; empty skips the check.

    Raises:
        DecompositionError: When an owner names no staffed role.
    """
    for sub in subtasks:
        detail = describe_unroutable_role(
            entity_id=sub.id,
            required_role=sub.required_role,
            available_roles=available_roles,
        )
        if detail is not None:
            logger.warning(DECOMPOSITION_LLM_PARSE_ERROR, error=detail)
            raise DecompositionError(detail)


def _args_to_plan(
    args: dict[str, JsonValue],
    parent_task_id: str,
    available_roles: tuple[NotBlankStr, ...] = (),
) -> DecompositionPlan:
    """Convert parsed arguments dict into a ``DecompositionPlan``.

    Args:
        args: Parsed tool call arguments or JSON content.
        parent_task_id: ID of the parent task.
        available_roles: The roles the org staffs, which every owner must be
            drawn from. Empty skips the check.

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
    _validate_roles(subtasks, available_roles)

    structure = _declared_structure(args)
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
    available_roles: tuple[NotBlankStr, ...] = (),
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
        available_roles: The roles the org staffs, which every owner must be
            drawn from. Empty skips the check.

    Returns:
        A validated ``DecompositionPlan``.

    Raises:
        DecompositionError: If the arguments are invalid.
    """
    try:
        return _args_to_plan(args, parent_task_id, available_roles)
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
    available_roles: tuple[NotBlankStr, ...] = (),
) -> DecompositionPlan:
    """Extract a plan from a tool call response.

    Looks for a tool call named ``submit_decomposition_plan``
    and parses its arguments into a ``DecompositionPlan``.

    Args:
        response: The LLM completion response.
        parent_task_id: ID of the parent task.
        available_roles: The roles the org staffs, which every owner must be
            drawn from. Empty skips the check.

    Returns:
        A validated ``DecompositionPlan``.

    Raises:
        DecompositionError: If no matching tool call is found
            or arguments are invalid.
    """
    for tc in response.tool_calls:
        if tc.name == TOOL_NAME:
            try:
                return _args_to_plan(tc.arguments, parent_task_id, available_roles)
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
    available_roles: tuple[NotBlankStr, ...] = (),
) -> DecompositionPlan:
    """Extract a plan from content text.

    Attempts to parse JSON directly, or from a markdown
    code fence.

    Args:
        response: The LLM completion response.
        parent_task_id: ID of the parent task.
        available_roles: The roles the org staffs, which every owner must be
            drawn from. Empty skips the check.

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
        return _args_to_plan(data, parent_task_id, available_roles)
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
