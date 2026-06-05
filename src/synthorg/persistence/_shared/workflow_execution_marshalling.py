"""Backend-agnostic marshalling for workflow executions.

The SQLite and Postgres workflow-execution repositories deserialise the
same ``workflow_executions`` columns into the same
:class:`WorkflowExecution` model. The JSON column diverges by backend:
SQLite stores ``node_executions`` as a TEXT JSON string, Postgres as
native JSONB (a Python ``list``). :func:`deserialize_node_executions`
absorbs both. Timestamps likewise differ (TEXT ISO vs ``TIMESTAMPTZ``)
and are normalised by :func:`coerce_row_timestamp`. The JSON-wrapping
on the write path (``json.dumps`` vs ``psycopg`` ``Jsonb``) stays in the
backend repos so this module never imports a driver.
"""

import json
from collections.abc import Mapping
from typing import LiteralString

from pydantic import ValidationError

from synthorg.core.enums import (
    WorkflowExecutionStatus,
    WorkflowNodeExecutionStatus,
    WorkflowNodeType,
)
from synthorg.core.persistence_errors import QueryError
from synthorg.engine.workflow.execution_models import (
    WorkflowExecution,
    WorkflowNodeExecution,
)
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.persistence import (
    PERSISTENCE_WORKFLOW_EXEC_DESERIALIZE_FAILED,
)
from synthorg.persistence._shared.datetime_marshaller import coerce_row_timestamp
from synthorg.persistence.workflow_execution_protocol import (
    WorkflowExecutionFilterSpec,
)

logger = get_logger(__name__)

WORKFLOW_EXECUTION_COLUMNS: LiteralString = """\
id, definition_id, definition_revision, status, node_executions,
activated_by, project, created_at, updated_at, completed_at,
error, version"""


def deserialize_node_executions(raw: object) -> tuple[WorkflowNodeExecution, ...]:
    """Deserialise the ``node_executions`` column into model instances.

    Accepts a JSON string (SQLite TEXT), an already-decoded list
    (Postgres JSONB), or ``None`` (treated as empty).

    Returns:
        Tuple of deserialised ``WorkflowNodeExecution`` instances.

    Raises:
        TypeError: If the column does not decode to a list, or if any
            entry is not a JSON object (corrupt entries surface as an
            error rather than being silently dropped).
    """
    if raw is None:
        items: object = []
    elif isinstance(raw, str):
        items = json.loads(raw)
    else:
        items = raw
    if not isinstance(items, list):
        msg = "node_executions must decode to a list"
        raise TypeError(msg)
    result: list[WorkflowNodeExecution] = []
    for item in items:
        if not isinstance(item, Mapping):
            msg = "node_executions entries must be JSON objects"
            raise TypeError(msg)
        result.append(
            WorkflowNodeExecution(
                node_id=item["node_id"],
                node_type=WorkflowNodeType(item["node_type"]),
                status=WorkflowNodeExecutionStatus(item["status"]),
                task_id=item.get("task_id"),
                skipped_reason=item.get("skipped_reason"),
            )
        )
    return tuple(result)


def node_execution_payloads(execution: WorkflowExecution) -> list[object]:
    """Build the JSON-serialisable node-execution payload list.

    Both backends wrap this list (``json.dumps`` for SQLite, ``Jsonb``
    for Postgres) at the write site.

    Returns:
        List of ``model_dump(mode="json")`` node-execution mappings.
    """
    return [ne.model_dump(mode="json") for ne in execution.node_executions]


def row_to_workflow_execution(
    data: dict[str, object], context_id: str
) -> WorkflowExecution:
    """Reconstruct a ``WorkflowExecution`` from a row mapping.

    Callers pass ``dict(row)``; this normalises the enum, node-execution
    list, and timestamps (string or native datetime) before validating.

    Returns:
        Validated ``WorkflowExecution`` model instance.

    Raises:
        QueryError: If deserialisation fails.
    """
    data = dict(data)
    try:
        data["status"] = WorkflowExecutionStatus(str(data["status"]))
        data["node_executions"] = deserialize_node_executions(
            data.get("node_executions")
        )
        data["created_at"] = coerce_row_timestamp(data["created_at"])
        data["updated_at"] = coerce_row_timestamp(data["updated_at"])
        if data.get("completed_at") is not None:
            data["completed_at"] = coerce_row_timestamp(data["completed_at"])
        return WorkflowExecution.model_validate(data)
    except (
        TypeError,
        ValueError,
        ValidationError,
        json.JSONDecodeError,
        KeyError,
    ) as exc:
        msg = f"Failed to deserialize workflow execution {context_id!r}"
        logger.warning(
            PERSISTENCE_WORKFLOW_EXEC_DESERIALIZE_FAILED,
            execution_id=context_id,
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        raise QueryError(msg) from exc


def build_workflow_execution_where(
    filter_spec: WorkflowExecutionFilterSpec, *, placeholder: LiteralString
) -> tuple[LiteralString, list[object]]:
    """Build the WHERE clause + bound params from a filter spec.

    Args:
        filter_spec: Optional ``definition_id`` / ``status`` filters.
        placeholder: The backend's bound-parameter token (``?`` / ``%s``).

    Returns:
        ``(where_clause, params)``: SQL fragment + positional params.
    """
    clauses: list[LiteralString] = []
    params: list[object] = []
    if filter_spec.definition_id is not None:
        clauses.append(f"definition_id = {placeholder}")
        params.append(filter_spec.definition_id)
    if filter_spec.status is not None:
        clauses.append(f"status = {placeholder}")
        params.append(filter_spec.status.value)
    where = " AND ".join(clauses) if clauses else "1=1"
    return where, params


__all__ = [
    "WORKFLOW_EXECUTION_COLUMNS",
    "build_workflow_execution_where",
    "deserialize_node_executions",
    "node_execution_payloads",
    "row_to_workflow_execution",
]
