"""Tests for subworkflow fields in YAML export output."""

from datetime import UTC, datetime

import pytest
import yaml

from synthorg.engine.workflow.definition import (
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowIODeclaration,
    WorkflowNode,
)
from synthorg.engine.workflow.enums import (
    WorkflowEdgeType,
    WorkflowNodeType,
    WorkflowType,
    WorkflowValueType,
)
from synthorg.engine.workflow.yaml_export import export_workflow_yaml
from tests._shared import as_uuid

_NOW = datetime(2026, 4, 1, 12, 0, 0, tzinfo=UTC)


def _minimal_parent_with_subworkflow_node() -> WorkflowDefinition:
    return WorkflowDefinition(
        id=as_uuid("wfdef-parent"),
        name="Parent Workflow",
        description="Uses a subworkflow",
        workflow_type=WorkflowType.SEQUENTIAL_PIPELINE,
        version="2.1.0",
        is_subworkflow=False,
        nodes=(
            WorkflowNode(
                id="start",
                type=WorkflowNodeType.START,
                label="Start",
            ),
            WorkflowNode(
                id="before",
                type=WorkflowNodeType.TASK,
                label="Before",
                config={"title": "Before", "task_type": "development"},
            ),
            WorkflowNode(
                id="call-sub",
                type=WorkflowNodeType.SUBWORKFLOW,
                label="Call Sub",
                config={
                    "subworkflow_id": "sub-finance-close",
                    "version": "1.0.0",
                    "input_bindings": {"quarter": "@parent.current_quarter"},
                    "output_bindings": {"report": "@child.report"},
                },
            ),
            WorkflowNode(
                id="end",
                type=WorkflowNodeType.END,
                label="End",
            ),
        ),
        edges=(
            WorkflowEdge(
                id="e1",
                source_node_id="start",
                target_node_id="before",
                type=WorkflowEdgeType.SEQUENTIAL,
            ),
            WorkflowEdge(
                id="e2",
                source_node_id="before",
                target_node_id="call-sub",
                type=WorkflowEdgeType.SEQUENTIAL,
            ),
            WorkflowEdge(
                id="e3",
                source_node_id="call-sub",
                target_node_id="end",
                type=WorkflowEdgeType.SEQUENTIAL,
            ),
        ),
        created_by="test",
        created_at=_NOW,
        updated_at=_NOW,
    )


def _subworkflow_definition() -> WorkflowDefinition:
    return WorkflowDefinition(
        id=as_uuid("sub-finance-close"),
        name="Finance Close",
        description="",
        workflow_type=WorkflowType.SEQUENTIAL_PIPELINE,
        version="1.0.0",
        is_subworkflow=True,
        inputs=(
            WorkflowIODeclaration(
                name="quarter",
                type=WorkflowValueType.STRING,
            ),
        ),
        outputs=(
            WorkflowIODeclaration(
                name="report",
                type=WorkflowValueType.STRING,
            ),
        ),
        nodes=(
            WorkflowNode(
                id="s",
                type=WorkflowNodeType.START,
                label="Start",
            ),
            WorkflowNode(
                id="t",
                type=WorkflowNodeType.TASK,
                label="Close",
                config={"title": "Close", "task_type": "admin"},
            ),
            WorkflowNode(
                id="e",
                type=WorkflowNodeType.END,
                label="End",
            ),
        ),
        edges=(
            WorkflowEdge(
                id="e1",
                source_node_id="s",
                target_node_id="t",
                type=WorkflowEdgeType.SEQUENTIAL,
            ),
            WorkflowEdge(
                id="e2",
                source_node_id="t",
                target_node_id="e",
                type=WorkflowEdgeType.SEQUENTIAL,
            ),
        ),
        created_by="test",
        created_at=_NOW,
        updated_at=_NOW,
    )


@pytest.mark.unit
class TestYamlExportSubworkflowFields:
    def test_parent_exports_subworkflow_node(self) -> None:
        parent = _minimal_parent_with_subworkflow_node()
        yaml_text = export_workflow_yaml(parent)
        doc = yaml.safe_load(yaml_text)

        body = doc["workflow_definition"]
        assert body["name"] == "Parent Workflow"
        assert body["version"] == "2.1.0"
        assert body["is_subworkflow"] is False

        steps = body["steps"]
        sub_step = next(s for s in steps if s["id"] == "call-sub")
        assert sub_step["type"] == "subworkflow"
        assert sub_step["subworkflow_id"] == "sub-finance-close"
        assert sub_step["version"] == "1.0.0"
        assert sub_step["input_bindings"] == {"quarter": "@parent.current_quarter"}
        assert sub_step["output_bindings"] == {"report": "@child.report"}

    def test_subworkflow_definition_exports_io_contract(self) -> None:
        sub = _subworkflow_definition()
        yaml_text = export_workflow_yaml(sub)
        doc = yaml.safe_load(yaml_text)

        body = doc["workflow_definition"]
        assert body["name"] == "Finance Close"
        assert body["version"] == "1.0.0"
        assert body["is_subworkflow"] is True
        assert len(body["inputs"]) == 1
        assert body["inputs"][0]["name"] == "quarter"
        assert body["inputs"][0]["type"] == "string"
        assert len(body["outputs"]) == 1
        assert body["outputs"][0]["name"] == "report"

    def test_parent_without_subworkflow_still_valid(self) -> None:
        """Parent without any SUBWORKFLOW node omits the io arrays."""
        definition = WorkflowDefinition(
            id=as_uuid("plain"),
            name="Plain",
            description="",
            workflow_type=WorkflowType.SEQUENTIAL_PIPELINE,
            version="1.0.0",
            is_subworkflow=False,
            nodes=(
                WorkflowNode(
                    id="start",
                    type=WorkflowNodeType.START,
                    label="Start",
                ),
                WorkflowNode(
                    id="t",
                    type=WorkflowNodeType.TASK,
                    label="T",
                    config={"title": "T", "task_type": "development"},
                ),
                WorkflowNode(
                    id="end",
                    type=WorkflowNodeType.END,
                    label="End",
                ),
            ),
            edges=(
                WorkflowEdge(
                    id="e1",
                    source_node_id="start",
                    target_node_id="t",
                    type=WorkflowEdgeType.SEQUENTIAL,
                ),
                WorkflowEdge(
                    id="e2",
                    source_node_id="t",
                    target_node_id="end",
                    type=WorkflowEdgeType.SEQUENTIAL,
                ),
            ),
            created_by="test",
            created_at=_NOW,
            updated_at=_NOW,
        )
        yaml_text = export_workflow_yaml(definition)
        doc = yaml.safe_load(yaml_text)
        body = doc["workflow_definition"]
        assert "inputs" not in body
        assert "outputs" not in body
        assert body["version"] == "1.0.0"
