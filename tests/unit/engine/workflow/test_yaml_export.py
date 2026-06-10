"""Tests for workflow definition YAML export."""

import pytest
import yaml

from synthorg.engine.workflow.enums import (
    WorkflowEdgeType,
    WorkflowNodeType,
    WorkflowType,
)
from synthorg.engine.workflow.yaml_export import export_workflow_yaml
from tests._shared import JsonDict
from tests.unit.engine.workflow.conftest import (
    make_edge as _edge,
)
from tests.unit.engine.workflow.conftest import (
    make_node as _node,
)
from tests.unit.engine.workflow.conftest import (
    make_workflow as _wf,
)


def _parse_yaml(yaml_str: str) -> JsonDict:
    return yaml.safe_load(yaml_str)  # type: ignore[no-any-return]


# ── Sequential pipeline ────────────────────────────────────────


class TestSequentialExport:
    """Export of sequential workflows."""

    @pytest.mark.unit
    def test_simple_pipeline(self) -> None:
        """start -> design -> implement -> end."""
        wf = _wf(
            nodes=(
                _node("s", WorkflowNodeType.START),
                _node("design", WorkflowNodeType.TASK, title="Design API"),
                _node("impl", WorkflowNodeType.TASK, title="Implement"),
                _node("e", WorkflowNodeType.END),
            ),
            edges=(
                _edge("e1", "s", "design"),
                _edge("e2", "design", "impl"),
                _edge("e3", "impl", "e"),
            ),
        )
        result = _parse_yaml(export_workflow_yaml(wf))
        doc = result["workflow_definition"]

        assert doc["name"] == "Test Workflow"
        assert doc["workflow_type"] == "sequential_pipeline"
        assert len(doc["steps"]) == 2

        # First step has no dependencies
        assert doc["steps"][0]["id"] == "design"
        assert doc["steps"][0]["title"] == "Design API"
        assert "depends_on" not in doc["steps"][0]

        # Second step depends on first
        assert doc["steps"][1]["id"] == "impl"
        assert doc["steps"][1]["depends_on"] == ["design"]

    @pytest.mark.unit
    def test_start_end_omitted(self) -> None:
        """START and END nodes should not appear in steps."""
        wf = _wf(
            nodes=(
                _node("s", WorkflowNodeType.START),
                _node("t", WorkflowNodeType.TASK, title="Work"),
                _node("e", WorkflowNodeType.END),
            ),
            edges=(
                _edge("e1", "s", "t"),
                _edge("e2", "t", "e"),
            ),
        )
        result = _parse_yaml(export_workflow_yaml(wf))
        step_types = [s["type"] for s in result["workflow_definition"]["steps"]]
        assert "start" not in step_types
        assert "end" not in step_types

    @pytest.mark.unit
    def test_description_included(self) -> None:
        wf = _wf(
            nodes=(
                _node("s", WorkflowNodeType.START),
                _node("t", WorkflowNodeType.TASK, title="Work"),
                _node("e", WorkflowNodeType.END),
            ),
            edges=(
                _edge("e1", "s", "t"),
                _edge("e2", "t", "e"),
            ),
            description="A test workflow",
        )
        result = _parse_yaml(export_workflow_yaml(wf))
        assert result["workflow_definition"]["description"] == "A test workflow"

    @pytest.mark.unit
    def test_empty_description_omitted(self) -> None:
        wf = _wf(
            nodes=(
                _node("s", WorkflowNodeType.START),
                _node("t", WorkflowNodeType.TASK, title="Work"),
                _node("e", WorkflowNodeType.END),
            ),
            edges=(
                _edge("e1", "s", "t"),
                _edge("e2", "t", "e"),
            ),
        )
        result = _parse_yaml(export_workflow_yaml(wf))
        assert "description" not in result["workflow_definition"]


# ── Parallel pipeline ──────────────────────────────────────────


class TestParallelExport:
    """Export of parallel workflows."""

    @pytest.mark.unit
    def test_split_join(self) -> None:
        """start -> split -> [a, b] -> join -> end."""
        wf = _wf(
            nodes=(
                _node("s", WorkflowNodeType.START),
                _node("split", WorkflowNodeType.PARALLEL_SPLIT),
                _node("a", WorkflowNodeType.TASK, title="Frontend"),
                _node("b", WorkflowNodeType.TASK, title="Backend"),
                _node("join", WorkflowNodeType.PARALLEL_JOIN, join_strategy="all"),
                _node("e", WorkflowNodeType.END),
            ),
            edges=(
                _edge("e1", "s", "split"),
                _edge("e2", "split", "a", WorkflowEdgeType.PARALLEL_BRANCH),
                _edge("e3", "split", "b", WorkflowEdgeType.PARALLEL_BRANCH),
                _edge("e4", "a", "join"),
                _edge("e5", "b", "join"),
                _edge("e6", "join", "e"),
            ),
            workflow_type=WorkflowType.PARALLEL_EXECUTION,
        )
        result = _parse_yaml(export_workflow_yaml(wf))
        doc = result["workflow_definition"]

        assert doc["workflow_type"] == "parallel_execution"

        split_step = next(s for s in doc["steps"] if s["type"] == "parallel_split")
        assert set(split_step["branches"]) == {"a", "b"}

        join_step = next(s for s in doc["steps"] if s["type"] == "parallel_join")
        assert join_step["join_strategy"] == "all"

    @pytest.mark.unit
    def test_join_default_strategy(self) -> None:
        """Join defaults to 'all' when no config."""
        wf = _wf(
            nodes=(
                _node("s", WorkflowNodeType.START),
                _node("split", WorkflowNodeType.PARALLEL_SPLIT),
                _node("a", WorkflowNodeType.TASK, title="A"),
                _node("b", WorkflowNodeType.TASK, title="B"),
                _node("join", WorkflowNodeType.PARALLEL_JOIN),  # No config
                _node("e", WorkflowNodeType.END),
            ),
            edges=(
                _edge("e1", "s", "split"),
                _edge("e2", "split", "a", WorkflowEdgeType.PARALLEL_BRANCH),
                _edge("e3", "split", "b", WorkflowEdgeType.PARALLEL_BRANCH),
                _edge("e4", "a", "join"),
                _edge("e5", "b", "join"),
                _edge("e6", "join", "e"),
            ),
        )
        result = _parse_yaml(export_workflow_yaml(wf))
        join_step = next(
            s
            for s in result["workflow_definition"]["steps"]
            if s["type"] == "parallel_join"
        )
        assert join_step["join_strategy"] == "all"


# ── Conditional pipeline ───────────────────────────────────────


class TestConditionalExport:
    """Export of conditional workflows."""

    @pytest.mark.unit
    def test_conditional_branches(self) -> None:
        wf = _wf(
            nodes=(
                _node("s", WorkflowNodeType.START),
                _node(
                    "cond",
                    WorkflowNodeType.CONDITIONAL,
                    condition_expression="status == 'approved'",
                ),
                _node("yes", WorkflowNodeType.TASK, title="Deploy"),
                _node("no", WorkflowNodeType.TASK, title="Fix"),
                _node("e", WorkflowNodeType.END),
            ),
            edges=(
                _edge("e1", "s", "cond"),
                _edge("e2", "cond", "yes", WorkflowEdgeType.CONDITIONAL_TRUE),
                _edge("e3", "cond", "no", WorkflowEdgeType.CONDITIONAL_FALSE),
                _edge("e4", "yes", "e"),
                _edge("e5", "no", "e"),
            ),
        )
        result = _parse_yaml(export_workflow_yaml(wf))
        cond_step = next(
            s
            for s in result["workflow_definition"]["steps"]
            if s["type"] == "conditional"
        )
        assert cond_step["condition"] == "status == 'approved'"


# ── Agent assignment ────────────────────────────────────────────


class TestAgentAssignmentExport:
    """Export of agent assignment nodes."""

    @pytest.mark.unit
    def test_assignment_node(self) -> None:
        wf = _wf(
            nodes=(
                _node("s", WorkflowNodeType.START),
                _node(
                    "assign",
                    WorkflowNodeType.AGENT_ASSIGNMENT,
                    routing_strategy="role_based",
                    role_filter="engineer",
                ),
                _node("t", WorkflowNodeType.TASK, title="Work"),
                _node("e", WorkflowNodeType.END),
            ),
            edges=(
                _edge("e1", "s", "assign"),
                _edge("e2", "assign", "t"),
                _edge("e3", "t", "e"),
            ),
        )
        result = _parse_yaml(export_workflow_yaml(wf))
        assign_step = next(
            s
            for s in result["workflow_definition"]["steps"]
            if s["type"] == "agent_assignment"
        )
        assert assign_step["strategy"] == "role_based"
        assert assign_step["role"] == "engineer"

    @pytest.mark.unit
    def test_task_with_embedded_assignment(self) -> None:
        wf = _wf(
            nodes=(
                _node("s", WorkflowNodeType.START),
                _node(
                    "t",
                    WorkflowNodeType.TASK,
                    title="Code Review",
                    routing_strategy="cost_optimized",
                    role_filter="senior_engineer",
                ),
                _node("e", WorkflowNodeType.END),
            ),
            edges=(
                _edge("e1", "s", "t"),
                _edge("e2", "t", "e"),
            ),
        )
        result = _parse_yaml(export_workflow_yaml(wf))
        task_step = result["workflow_definition"]["steps"][0]
        assert task_step["agent_assignment"]["strategy"] == "cost_optimized"
        assert task_step["agent_assignment"]["role"] == "senior_engineer"


# ── Task config fields ─────────────────────────────────────────


class TestTaskConfigExport:
    """Task config fields in export."""

    @pytest.mark.unit
    def test_full_task_config(self) -> None:
        wf = _wf(
            nodes=(
                _node("s", WorkflowNodeType.START),
                _node(
                    "t",
                    WorkflowNodeType.TASK,
                    title="Design",
                    task_type="design",
                    priority="high",
                    complexity="medium",
                    coordination_topology="centralized",
                ),
                _node("e", WorkflowNodeType.END),
            ),
            edges=(
                _edge("e1", "s", "t"),
                _edge("e2", "t", "e"),
            ),
        )
        result = _parse_yaml(export_workflow_yaml(wf))
        step = result["workflow_definition"]["steps"][0]
        assert step["title"] == "Design"
        assert step["task_type"] == "design"
        assert step["priority"] == "high"
        assert step["complexity"] == "medium"
        assert step["coordination_topology"] == "centralized"


# ── Topological ordering ───────────────────────────────────────


class TestTopologicalOrder:
    """Steps appear in topological order."""

    @pytest.mark.unit
    def test_order_preserved(self) -> None:
        """a -> b -> c should export in that order."""
        wf = _wf(
            nodes=(
                _node("s", WorkflowNodeType.START),
                _node("c", WorkflowNodeType.TASK, title="C"),
                _node("a", WorkflowNodeType.TASK, title="A"),
                _node("b", WorkflowNodeType.TASK, title="B"),
                _node("e", WorkflowNodeType.END),
            ),
            edges=(
                _edge("e1", "s", "a"),
                _edge("e2", "a", "b"),
                _edge("e3", "b", "c"),
                _edge("e4", "c", "e"),
            ),
        )
        result = _parse_yaml(export_workflow_yaml(wf))
        step_ids = [s["id"] for s in result["workflow_definition"]["steps"]]
        assert step_ids.index("a") < step_ids.index("b") < step_ids.index("c")


# ── Cycle rejection ────────────────────────────────────────────


class TestCycleRejection:
    """Export raises on cyclic graphs."""

    @pytest.mark.unit
    def test_cycle_raises(self) -> None:
        wf = _wf(
            nodes=(
                _node("s", WorkflowNodeType.START),
                _node("a", WorkflowNodeType.TASK, title="A"),
                _node("b", WorkflowNodeType.TASK, title="B"),
                _node("e", WorkflowNodeType.END),
            ),
            edges=(
                _edge("e1", "s", "a"),
                _edge("e2", "a", "b"),
                _edge("e3", "b", "a"),
            ),
        )
        with pytest.raises(ValueError, match="cycle"):
            export_workflow_yaml(wf)


# ── YAML output format ─────────────────────────────────────────


class TestYamlFormat:
    """YAML output is well-formed."""

    @pytest.mark.unit
    def test_parseable_yaml(self) -> None:
        wf = _wf(
            nodes=(
                _node("s", WorkflowNodeType.START),
                _node("t", WorkflowNodeType.TASK, title="Work"),
                _node("e", WorkflowNodeType.END),
            ),
            edges=(
                _edge("e1", "s", "t"),
                _edge("e2", "t", "e"),
            ),
        )
        yaml_str = export_workflow_yaml(wf)
        assert isinstance(yaml_str, str)
        parsed = yaml.safe_load(yaml_str)
        assert "workflow_definition" in parsed

    @pytest.mark.unit
    def test_no_start_end_in_steps(self) -> None:
        wf = _wf(
            nodes=(
                _node("s", WorkflowNodeType.START),
                _node("t", WorkflowNodeType.TASK, title="Work"),
                _node("e", WorkflowNodeType.END),
            ),
            edges=(
                _edge("e1", "s", "t"),
                _edge("e2", "t", "e"),
            ),
        )
        result = _parse_yaml(export_workflow_yaml(wf))
        step_ids = {s["id"] for s in result["workflow_definition"]["steps"]}
        assert "s" not in step_ids
        assert "e" not in step_ids
