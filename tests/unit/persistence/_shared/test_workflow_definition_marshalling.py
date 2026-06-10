"""Tests for the shared workflow-definition marshalling helpers."""

from datetime import UTC, datetime

import pytest

from synthorg.core.persistence_errors import QueryError
from synthorg.engine.workflow.definition import (
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)
from synthorg.engine.workflow.enums import (
    WorkflowEdgeType,
    WorkflowNodeType,
    WorkflowType,
)
from synthorg.persistence._shared.workflow_definition_marshalling import (
    build_workflow_definition_where,
    definition_jsonb_payloads,
    row_to_workflow_definition,
    serialize_definition_columns,
)
from synthorg.persistence.workflow_definition_protocol import (
    WorkflowDefinitionFilterSpec,
)
from tests._shared import as_uuid

_NOW = datetime(2026, 5, 22, 12, 0, tzinfo=UTC)


def _definition() -> WorkflowDefinition:
    """A valid START -> TASK -> END workflow definition."""
    return WorkflowDefinition(
        id=as_uuid("wfdef-1"),
        name="My workflow",
        description="A workflow.",
        workflow_type=WorkflowType.SEQUENTIAL_PIPELINE,
        version="1.0.0",
        nodes=(
            WorkflowNode(
                id="wfdef-1-start", type=WorkflowNodeType.START, label="Start"
            ),
            WorkflowNode(id="wfdef-1-task", type=WorkflowNodeType.TASK, label="Work"),
            WorkflowNode(id="wfdef-1-end", type=WorkflowNodeType.END, label="End"),
        ),
        edges=(
            WorkflowEdge(
                id="wfdef-1-e1",
                source_node_id="wfdef-1-start",
                target_node_id="wfdef-1-task",
                type=WorkflowEdgeType.SEQUENTIAL,
            ),
            WorkflowEdge(
                id="wfdef-1-e2",
                source_node_id="wfdef-1-task",
                target_node_id="wfdef-1-end",
                type=WorkflowEdgeType.SEQUENTIAL,
            ),
        ),
        created_by="user-1",
        created_at=_NOW,
        updated_at=_NOW,
        is_subworkflow=True,
        revision=1,
    )


def _sqlite_data(definition: WorkflowDefinition) -> dict[str, object]:
    """A SQLite-shaped row: JSON TEXT columns and ISO timestamps."""
    nodes, edges, inputs, outputs = serialize_definition_columns(definition)
    return {
        "id": str(definition.id),
        "name": definition.name,
        "description": definition.description,
        "workflow_type": definition.workflow_type.value,
        "version": definition.version,
        "inputs": inputs,
        "outputs": outputs,
        "is_subworkflow": 1 if definition.is_subworkflow else 0,
        "nodes": nodes,
        "edges": edges,
        "created_by": definition.created_by,
        "created_at": definition.created_at.isoformat(),
        "updated_at": definition.updated_at.isoformat(),
        "revision": definition.revision,
    }


@pytest.mark.unit
class TestRowToWorkflowDefinition:
    """``row_to_workflow_definition`` reconstructs from either backend shape."""

    def test_sqlite_round_trip(self) -> None:
        definition = _definition()
        result = row_to_workflow_definition(
            _sqlite_data(definition), str(definition.id)
        )

        assert result == definition

    def test_postgres_native_shape(self) -> None:
        definition = _definition()
        data = _sqlite_data(definition)
        nodes, edges, inputs, outputs = definition_jsonb_payloads(definition)
        data["nodes"] = nodes
        data["edges"] = edges
        data["inputs"] = inputs
        data["outputs"] = outputs
        data["is_subworkflow"] = definition.is_subworkflow
        data["created_at"] = _NOW
        data["updated_at"] = _NOW

        result = row_to_workflow_definition(data, str(definition.id))

        assert result == definition

    def test_corrupt_workflow_type_raises(self) -> None:
        data = _sqlite_data(_definition())
        data["workflow_type"] = "not-a-type"

        with pytest.raises(QueryError):
            row_to_workflow_definition(data, "ctx")

    def test_non_list_json_column_raises(self) -> None:
        data = _sqlite_data(_definition())
        data["nodes"] = '{"not": "a list"}'

        with pytest.raises(QueryError):
            row_to_workflow_definition(data, "ctx")


@pytest.mark.unit
class TestBuildWorkflowDefinitionWhere:
    """``build_workflow_definition_where`` emits an optional WHERE fragment."""

    def test_empty_filter_no_where(self) -> None:
        where, params = build_workflow_definition_where(
            WorkflowDefinitionFilterSpec(), placeholder="?"
        )

        assert where == ""
        assert params == []

    def test_type_filter_sqlite(self) -> None:
        spec = WorkflowDefinitionFilterSpec(
            workflow_type=WorkflowType.SEQUENTIAL_PIPELINE
        )
        where, params = build_workflow_definition_where(spec, placeholder="?")

        assert where == " WHERE workflow_type = ?"
        assert params == [WorkflowType.SEQUENTIAL_PIPELINE.value]

    def test_type_filter_postgres(self) -> None:
        spec = WorkflowDefinitionFilterSpec(
            workflow_type=WorkflowType.PARALLEL_EXECUTION
        )
        where, params = build_workflow_definition_where(spec, placeholder="%s")

        assert where == " WHERE workflow_type = %s"
        assert params == [WorkflowType.PARALLEL_EXECUTION.value]
