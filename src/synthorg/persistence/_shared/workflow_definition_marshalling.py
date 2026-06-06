"""Backend-agnostic marshalling for workflow definitions.

The SQLite and Postgres workflow-definition repositories deserialise the
same ``workflow_definitions`` columns into the same
:class:`WorkflowDefinition` model. The ``nodes`` / ``edges`` / ``inputs``
/ ``outputs`` columns diverge by backend: SQLite stores TEXT JSON
strings, Postgres native JSONB (Python lists). :func:`row_to_workflow_definition`
absorbs both, and :func:`serialize_definition_columns` /
:func:`definition_jsonb_payloads` provide the matching write payloads
(the ``json.dumps`` vs ``Jsonb`` wrapping stays in the backend repos so
this module never imports a driver).
"""

import json
from typing import LiteralString

from pydantic import ValidationError

from synthorg.core.enums import WorkflowType
from synthorg.core.persistence_errors import QueryError
from synthorg.engine.workflow.definition import (
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowIODeclaration,
    WorkflowNode,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence.workflow_def import (
    PERSISTENCE_WORKFLOW_DEF_DESERIALIZE_FAILED,
)
from synthorg.persistence._shared.datetime_marshaller import coerce_row_timestamp
from synthorg.persistence.workflow_definition_protocol import (
    WorkflowDefinitionFilterSpec,
)

logger = get_logger(__name__)

WORKFLOW_DEFINITION_COLUMNS: LiteralString = """\
id, name, description, workflow_type, version, inputs, outputs,
is_subworkflow, nodes, edges, created_by, created_at, updated_at, revision"""


def _decode_json_list(raw: object) -> list[object]:
    """Decode a JSON-array column to a Python list (str or native).

    Returns:
        The decoded list (empty when the column is ``None``).

    Raises:
        TypeError: If the column does not decode to a list.
    """
    if raw is None:
        return []
    decoded = json.loads(raw) if isinstance(raw, str) else raw
    if not isinstance(decoded, list):
        msg = "workflow definition JSON column must decode to a list"
        raise TypeError(msg)
    return decoded


def row_to_workflow_definition(
    data: dict[str, object], context_id: str
) -> WorkflowDefinition:
    """Reconstruct a ``WorkflowDefinition`` from a row mapping.

    Callers pass ``dict(row)``; node/edge/IO columns may be TEXT JSON
    (SQLite) or native lists (Postgres), and timestamps TEXT or native
    datetime.

    Returns:
        Validated ``WorkflowDefinition`` model instance.

    Raises:
        QueryError: If deserialisation fails.
    """
    data = dict(data)
    try:
        data["workflow_type"] = WorkflowType(str(data["workflow_type"]))
        data["nodes"] = tuple(
            WorkflowNode.model_validate(n) for n in _decode_json_list(data.get("nodes"))
        )
        data["edges"] = tuple(
            WorkflowEdge.model_validate(e) for e in _decode_json_list(data.get("edges"))
        )
        data["inputs"] = tuple(
            WorkflowIODeclaration.model_validate(i)
            for i in _decode_json_list(data.get("inputs"))
        )
        data["outputs"] = tuple(
            WorkflowIODeclaration.model_validate(o)
            for o in _decode_json_list(data.get("outputs"))
        )
        data["is_subworkflow"] = bool(data.get("is_subworkflow"))
        data["created_at"] = coerce_row_timestamp(data["created_at"])
        data["updated_at"] = coerce_row_timestamp(data["updated_at"])
        return WorkflowDefinition.model_validate(data)
    except (
        ValueError,
        ValidationError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
    ) as exc:
        msg = f"Failed to deserialize workflow definition {context_id!r}"
        logger.warning(
            PERSISTENCE_WORKFLOW_DEF_DESERIALIZE_FAILED,
            definition_id=context_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc


def definition_jsonb_payloads(
    definition: WorkflowDefinition,
) -> tuple[list[object], list[object], list[object], list[object]]:
    """Build ``(nodes, edges, inputs, outputs)`` as JSON-ready lists.

    The Postgres repo wraps each in ``Jsonb``; the SQLite payload helper
    :func:`serialize_definition_columns` JSON-encodes them.

    Returns:
        Four lists of ``model_dump(mode="json")`` mappings.
    """
    return (
        [n.model_dump(mode="json") for n in definition.nodes],
        [e.model_dump(mode="json") for e in definition.edges],
        [i.model_dump(mode="json") for i in definition.inputs],
        [o.model_dump(mode="json") for o in definition.outputs],
    )


def serialize_definition_columns(
    definition: WorkflowDefinition,
) -> tuple[str, str, str, str]:
    """Build ``(nodes, edges, inputs, outputs)`` as JSON TEXT (SQLite).

    Returns:
        Four JSON-encoded strings in node/edge/input/output order.
    """
    nodes, edges, inputs, outputs = definition_jsonb_payloads(definition)
    return (
        json.dumps(nodes),
        json.dumps(edges),
        json.dumps(inputs),
        json.dumps(outputs),
    )


def build_workflow_definition_where(
    filter_spec: WorkflowDefinitionFilterSpec, *, placeholder: LiteralString
) -> tuple[LiteralString, list[object]]:
    """Build the optional WHERE fragment + bound params.

    Args:
        filter_spec: Carries the optional ``workflow_type`` filter.
        placeholder: The backend's bound-parameter token (``?`` / ``%s``).

    Returns:
        ``(where_fragment, params)`` where ``where_fragment`` is a
        ``" WHERE ..."``-prefixed clause or ``""`` when no filter applies.
    """
    conditions: list[LiteralString] = []
    params: list[object] = []
    if filter_spec.workflow_type is not None:
        conditions.append(f"workflow_type = {placeholder}")
        params.append(filter_spec.workflow_type.value)
    where_fragment = " WHERE " + " AND ".join(conditions) if conditions else ""
    return where_fragment, params


__all__ = [
    "WORKFLOW_DEFINITION_COLUMNS",
    "build_workflow_definition_where",
    "definition_jsonb_payloads",
    "row_to_workflow_definition",
    "serialize_definition_columns",
]
