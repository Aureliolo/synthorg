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
from pydantic import ValidationError as PydanticValidationError

from synthorg.core.plan_validation import (
    ORDERED_STRUCTURES,
    describe_structureless_graph,
    describe_undecidable_criterion,
    describe_unroutable_role,
    describe_unstated_reference,
)
from synthorg.core.task_enums import CoordinationTopology, TaskStructure
from synthorg.core.types import NotBlankStr
from synthorg.engine.decomposition._plan_output_guard import (
    guard_plan_text,
    guard_plan_texts,
    plan_style_refusal,
)
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
from synthorg.engine.output_style.errors import OutputPolicyViolationError
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


def _validate_graph(
    subtasks: tuple[SubtaskDefinition, ...],
    structure: TaskStructure,
) -> None:
    """Reject a plan whose graph contradicts what the plan says about itself.

    Both checks are correctable in-session for the same reason the roster
    check is: the planner can restate the dependencies it meant. Discovered
    at dispatch instead, the plan has already been approved by an operator
    who was shown an ordering that does not exist.

    Args:
        subtasks: The parsed subtasks, in plan order.
        structure: The structure the planner declared.

    Raises:
        DecompositionError: When an ordered structure carries no edges, an
            item names another it declares no dependency on, or an item's own
            gate demands evidence the plan produces after it.
    """
    detail = describe_structureless_graph(
        declared_sequential=structure in ORDERED_STRUCTURES,
        units=subtasks,
    )
    if detail is None:
        for subtask in subtasks:
            detail = describe_unstated_reference(unit=subtask, others=subtasks)
            if detail is not None:
                break
    if detail is None:
        for subtask in subtasks:
            detail = describe_undecidable_criterion(unit=subtask, others=subtasks)
            if detail is not None:
                break
    if detail is None:
        return
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
    _validate_graph(subtasks, structure)
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


def _guarded_plan(
    args: dict[str, JsonValue],
    parent_task_id: str,
    available_roles: tuple[NotBlankStr, ...] = (),
) -> DecompositionPlan:
    """Parse *args* into a plan whose prose passes the output-style policy.

    Every plan the product accepts comes through here, from the submit tool
    and from the tool-less fallback alike, and both callers can still ask the
    producer for a better one: the tool turns the refusal into a correctable
    error, the fallback into a retry. That is why the check lives here rather
    than at the mapping to the durable plan, where the producer has gone and
    the only thing left to refuse is a finished decomposition.

    Args:
        args: Parsed tool call arguments or JSON content.
        parent_task_id: ID of the parent task.
        available_roles: The roles the org staffs.

    Returns:
        A validated ``DecompositionPlan`` fit to render.

    Raises:
        DecompositionError: If the arguments are invalid, or the plan's
            wording breaks a hard output-style rule.
    """
    plan = _args_to_plan(args, parent_task_id, available_roles)
    try:
        return _revalidated(_style_checked(plan))
    except OutputPolicyViolationError as exc:
        detail = plan_style_refusal(exc)
        logger.warning(
            DECOMPOSITION_LLM_PARSE_ERROR,
            error=detail,
            parent_task_id=parent_task_id,
        )
        raise DecompositionError(detail) from exc


def _revalidated(plan: DecompositionPlan) -> DecompositionPlan:
    """Re-judge a style-rewritten plan on the text it now carries.

    An AUTO_REWRITE rule substitutes a span, and the guard applies it through
    ``model_copy(update=...)``, which does not validate; ``NotBlankStr`` is an
    annotation that only runs inside a model, so wrapping the result validates
    nothing either. A rule whose replacement empties the span therefore lands
    blank prose on a plan an operator is then shown.

    The graph checks have the same problem one level up: they ran inside
    ``_args_to_plan``, before the rewrite, and one of them reads artefact names
    out of the acceptance criteria, so a rewritten token leaves the plan judged
    decidable on text it no longer carries.

    Args:
        plan: The plan as the style guard left it.

    Returns:
        The same plan, re-validated field by field.

    Raises:
        DecompositionError: When the rewritten plan no longer validates, or its
            graph no longer holds.
    """
    try:
        revalidated = DecompositionPlan.model_validate(plan.model_dump())
    except PydanticValidationError as exc:
        detail = (
            "The house style rewrite left the plan invalid: "
            f"{safe_error_description(exc)}"
        )
        logger.warning(DECOMPOSITION_LLM_PARSE_ERROR, error=detail)
        raise DecompositionError(detail) from exc
    _validate_graph(revalidated.subtasks, revalidated.task_structure)
    return revalidated


def _style_checked(plan: DecompositionPlan) -> DecompositionPlan:
    """Return *plan* with its prose passed through the output-style guard.

    Artefact paths are deliberately not prose: a file name is read by a tool
    before a person reads it, so rewriting one renames the deliverable.

    Returns:
        The plan, carrying any auto-rewrite a rule resolved.

    Raises:
        OutputPolicyViolationError: When a non-exempt hard rule blocks.
    """
    subtasks = tuple(
        subtask.model_copy(
            update={
                "title": guard_plan_text(subtask.title),
                "description": guard_plan_text(subtask.description),
                "acceptance_criteria": guard_plan_texts(subtask.acceptance_criteria),
            }
        )
        for subtask in plan.subtasks
    )
    return plan.model_copy(
        update={
            "subtasks": subtasks,
            "open_questions": guard_plan_texts(plan.open_questions),
            "assumptions": guard_plan_texts(plan.assumptions),
        }
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
        return _guarded_plan(args, parent_task_id, available_roles)
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
                return _guarded_plan(tc.arguments, parent_task_id, available_roles)
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


def _embedded_json_object(text: str) -> dict[str, JsonValue] | None:
    """Return the first complete JSON object embedded in *text*.

    Scanned rather than matched with a regular expression, because braces
    nest: a plan carries subtask objects and option objects, so the first
    closing brace is nowhere near the end of the object that opened.

    String literals are tracked so a brace inside a description ("clear a
    line {sic}") does not end the scan, and an escape inside a string is
    skipped so a trailing backslash before a quote cannot close it early.

    Args:
        text: The model's content, already stripped of any fence.

    Returns:
        The decoded object, or ``None`` when the text holds no complete one
        or holds something that is not an object.
    """
    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return _decoded_object(text[start : index + 1])
    return None


def _decoded_object(candidate: str) -> dict[str, JsonValue] | None:
    """Decode *candidate* when it is a JSON object.

    Returns:
        The object, or ``None`` when it does not decode or decodes to
        something else (a bare array of subtasks is not a plan).
    """
    try:
        decoded: JsonValue = json.loads(candidate)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


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

    raw = response.content.strip()
    text = raw

    match = _MARKDOWN_FENCE_RE.search(text)
    fenced = match is not None
    if match:
        text = match.group(1).strip()

    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        # The prompt asks for "a JSON object" when a tool call is not
        # possible, and a model answering in prose puts one INSIDE a
        # sentence: "Here is the plan: {...}". Refusing that reads as the
        # model having produced nothing, when it produced the plan and a
        # greeting. Retrying cannot fix it either: the same prompt to the
        # same model returns the same shape, so three attempts buy latency
        # and nothing else.
        embedded = _embedded_json_object(text)
        if embedded is None:
            # What it looked like, never what it said: the content is model
            # output over attacker-influenced input, so its shape is
            # diagnosable and its text is not.
            msg = f"Failed to parse JSON from content: {safe_error_description(exc)}"
            logger.warning(
                DECOMPOSITION_LLM_PARSE_ERROR,
                error=msg,
                parent_task_id=parent_task_id,
                content_length=len(raw),
                fenced=fenced,
                starts_with_brace=text.startswith("{"),
            )
            raise DecompositionError(msg) from exc
        data = embedded

    if not isinstance(data, dict):
        # The embedded path refuses a non-object; the direct decode did not,
        # so a payload that parsed cleanly to a list, a string or a number
        # went on under an annotation that had stopped describing it, and the
        # refusal surfaced two frames down as an AttributeError the broad
        # handler swallowed into a message about parsing.
        msg = "Failed to parse plan from content: the JSON is not an object"
        logger.warning(
            DECOMPOSITION_LLM_PARSE_ERROR,
            error=msg,
            parent_task_id=parent_task_id,
            content_length=len(raw),
            fenced=fenced,
            decoded_type=type(data).__name__,
        )
        raise DecompositionError(msg)

    try:
        return _guarded_plan(data, parent_task_id, available_roles)
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
