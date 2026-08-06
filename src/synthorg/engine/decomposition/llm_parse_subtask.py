# module-kind: code
"""Subtask-level parsing for LLM-based decomposition.

The item half of :mod:`synthorg.engine.decomposition.llm_parse`: turning one
raw subtask dict from a tool call into a validated
:class:`~synthorg.engine.decomposition.models.SubtaskDefinition`, and remapping
the model-assigned ids across the whole set. The plan-level half (the response
envelope, the plan's own fields) stays in ``llm_parse``.

``enum_or_default`` lives here because both halves resolve enums the same
lenient way: an unrecognised value is a logged default, never a raised error,
so one bad token in an otherwise good plan does not cost a whole re-prompt.
The sibling per-field readers ``required_field`` and ``string_array`` are
strict for the opposite reason: a missing required field or a non-array where
an array belongs is the model failing to answer, not answering awkwardly.
"""

from enum import Enum
from typing import Final
from uuid import uuid4

from pydantic import JsonValue

from synthorg.core.plan_enums import PlanItemKind
from synthorg.core.task_enums import Complexity, Stakes
from synthorg.engine.decomposition.models import SubtaskDefinition
from synthorg.engine.errors import DecompositionError
from synthorg.observability import get_logger
from synthorg.observability.events.decomposition import (
    DECOMPOSITION_LLM_PARSE_ERROR,
)

logger = get_logger(__name__)

_COMPLEXITY_MAP: Final[dict[str, Complexity]] = {c.value: c for c in Complexity}

_STAKES_MAP: Final[dict[str, Stakes]] = {s.value: s for s in Stakes}

_PLAN_ITEM_KIND_MAP: Final[dict[str, PlanItemKind]] = {k.value: k for k in PlanItemKind}


def enum_or_default[E: Enum](
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


def string_array(raw: dict[str, JsonValue], field: str) -> tuple[str, ...]:
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


def required_field(raw: dict[str, JsonValue], field: str) -> JsonValue:
    """Read a subtask field the schema requires, raising when it is absent.

    Args:
        raw: The raw subtask dict from tool call arguments.
        field: The required field name to read.

    Returns:
        The field's raw value.

    Raises:
        DecompositionError: When the field is absent. The available keys are
            named because the usual cause is the model spelling one of them
            differently, which the bare field name alone does not reveal.
    """
    if field not in raw:
        msg = (
            f"Subtask missing required field '{field}'. "
            f"Available keys: {sorted(raw.keys())}"
        )
        logger.warning(DECOMPOSITION_LLM_PARSE_ERROR, error=msg)
        raise DecompositionError(msg)
    return raw[field]


def parse_subtask(raw: dict[str, JsonValue]) -> SubtaskDefinition:
    """Convert a raw subtask dict into a ``SubtaskDefinition``.

    Args:
        raw: Dict from LLM tool call arguments.

    Returns:
        A validated ``SubtaskDefinition``.

    Raises:
        DecompositionError: If a required field is missing, ``dependencies``
            or ``required_skills`` is present but not a list, or
            ``acceptance_criteria`` resolves empty.
    """
    subtask_id = required_field(raw, "id")
    title = required_field(raw, "title")
    description = required_field(raw, "description")
    complexity = enum_or_default(
        raw.get("estimated_complexity", "medium"),
        _COMPLEXITY_MAP,
        Complexity.MEDIUM,
        field="complexity",
    )
    stakes = enum_or_default(
        raw.get("stakes", "normal"),
        _STAKES_MAP,
        Stakes.NORMAL,
        field="stakes",
    )
    kind = enum_or_default(
        raw.get("kind", "work"),
        _PLAN_ITEM_KIND_MAP,
        PlanItemKind.WORK,
        field="kind",
    )
    deps = string_array(raw, "dependencies")
    skills = string_array(raw, "required_skills")
    artifacts = string_array(raw, "expected_artifacts")
    acceptance = string_array(raw, "acceptance_criteria")
    if not acceptance:
        msg = (
            f"Subtask {subtask_id!r} has no acceptance_criteria; every plan item "
            "must state a verifiable definition of done"
        )
        logger.warning(DECOMPOSITION_LLM_PARSE_ERROR, error=msg)
        raise DecompositionError(msg)
    return SubtaskDefinition.model_validate(
        {
            "id": subtask_id,
            "title": title,
            "description": description,
            "dependencies": deps,
            "estimated_complexity": complexity,
            "stakes": stakes,
            "kind": kind,
            "options": raw.get("options") or (),
            "required_skills": skills,
            "required_role": raw.get("required_role"),
            "expected_artifacts": artifacts,
            "acceptance_criteria": acceptance,
            "satisfies": string_array(raw, "satisfies"),
        }
    )


def remap_subtask_ids(
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


__all__ = [
    "enum_or_default",
    "parse_subtask",
    "remap_subtask_ids",
    "required_field",
    "string_array",
]
