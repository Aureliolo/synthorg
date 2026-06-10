"""Tests for the verification-pipeline blueprint."""

import pytest

from synthorg.engine.workflow.blueprint_loader import load_blueprint
from synthorg.engine.workflow.definition import WorkflowDefinition
from synthorg.engine.workflow.enums import WorkflowEdgeType, WorkflowNodeType
from synthorg.engine.workflow.validation import validate_workflow
from tests._shared import as_uuid


@pytest.mark.unit
class TestVerificationPipelineBlueprint:
    def test_loads_successfully(self) -> None:
        bp = load_blueprint("verification-pipeline")
        assert bp.name == "verification-pipeline"

    def test_has_verification_node(self) -> None:
        bp = load_blueprint("verification-pipeline")
        types = [n.type for n in bp.nodes]
        assert WorkflowNodeType.VERIFICATION in types

    def test_verification_node_has_three_edges(self) -> None:
        bp = load_blueprint("verification-pipeline")
        verify_node = next(
            n for n in bp.nodes if n.type == WorkflowNodeType.VERIFICATION
        )
        out_edges = [e for e in bp.edges if e.source_node_id == verify_node.id]
        edge_types = {e.type for e in out_edges}
        assert edge_types == {
            WorkflowEdgeType.VERIFICATION_PASS,
            WorkflowEdgeType.VERIFICATION_FAIL,
            WorkflowEdgeType.VERIFICATION_REFER,
        }

    def test_has_planner_generator_evaluator_assignments(self) -> None:
        bp = load_blueprint("verification-pipeline")
        assignments = [
            n for n in bp.nodes if n.type == WorkflowNodeType.AGENT_ASSIGNMENT
        ]
        agent_names = {n.config.get("agent_name") for n in assignments}
        assert "planner-agent" in agent_names
        assert "generator-agent" in agent_names
        assert "evaluator-agent" in agent_names

    def test_passes_graph_validation(self) -> None:
        bp = load_blueprint("verification-pipeline")
        nodes = tuple(
            {"id": n.id, "type": n.type, "label": n.label, "config": dict(n.config)}
            for n in bp.nodes
        )
        edges = tuple(
            {
                "id": e.id,
                "source_node_id": e.source_node_id,
                "target_node_id": e.target_node_id,
                "type": e.type,
            }
            for e in bp.edges
        )
        defn = WorkflowDefinition.model_validate(
            {
                "id": as_uuid("wf-test"),
                "name": bp.name,
                "created_by": "test",
                "nodes": nodes,
                "edges": edges,
            }
        )
        result = validate_workflow(defn)
        assert result.valid, [e.message for e in result.errors]

    def test_verification_node_config(self) -> None:
        bp = load_blueprint("verification-pipeline")
        verify_node = next(
            n for n in bp.nodes if n.type == WorkflowNodeType.VERIFICATION
        )
        assert verify_node.config.get("rubric_name") == "default-task"
        assert verify_node.config.get("evaluator_agent_id") == "evaluator-agent"
