"""Tests for visual workflow definition models."""

import pytest
from pydantic import ValidationError

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
from tests.unit.engine.workflow.conftest import (
    make_edge as _edge,
)
from tests.unit.engine.workflow.conftest import (
    make_end_node as _end_node,
)
from tests.unit.engine.workflow.conftest import (
    make_minimal_definition as _minimal_definition,
)
from tests.unit.engine.workflow.conftest import (
    make_start_node as _start_node,
)
from tests.unit.engine.workflow.conftest import (
    make_task_node as _task_node,
)

# ── WorkflowNode ────────────────────────────────────────────────


class TestWorkflowNode:
    """WorkflowNode validation."""

    @pytest.mark.unit
    def test_valid_node(self) -> None:
        node = _task_node()
        assert node.id == "task-1"
        assert node.type == WorkflowNodeType.TASK
        assert node.label == "Do work"
        assert node.position_x == 100.0
        assert node.position_y == 200.0
        assert node.config["task_type"] == "development"

    @pytest.mark.unit
    def test_default_position(self) -> None:
        node = _start_node()
        assert node.position_x == 0.0
        assert node.position_y == 0.0

    @pytest.mark.unit
    def test_default_config(self) -> None:
        node = _start_node()
        assert dict(node.config) == {}

    @pytest.mark.unit
    def test_blank_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must not be whitespace"):
            WorkflowNode(id="  ", type=WorkflowNodeType.TASK, label="X")

    @pytest.mark.unit
    def test_empty_id_rejected(self) -> None:
        with pytest.raises(ValidationError):
            WorkflowNode(id="", type=WorkflowNodeType.TASK, label="X")

    @pytest.mark.unit
    def test_blank_label_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must not be whitespace"):
            WorkflowNode(id="n1", type=WorkflowNodeType.TASK, label="  ")

    @pytest.mark.unit
    def test_frozen(self) -> None:
        node = _task_node()
        with pytest.raises(ValidationError, match="frozen"):
            node.label = "changed"  # type: ignore[misc]

    @pytest.mark.unit
    def test_nan_position_rejected(self) -> None:
        with pytest.raises(ValidationError, match="finite_number"):
            WorkflowNode(
                id="n1",
                type=WorkflowNodeType.TASK,
                label="X",
                position_x=float("nan"),
            )

    @pytest.mark.unit
    @pytest.mark.parametrize("node_type", list(WorkflowNodeType))
    def test_all_node_types_accepted(self, node_type: WorkflowNodeType) -> None:
        node = WorkflowNode(id="n1", type=node_type, label="X")
        assert node.type == node_type


# ── WorkflowEdge ────────────────────────────────────────────────


class TestWorkflowEdge:
    """WorkflowEdge validation."""

    @pytest.mark.unit
    def test_valid_edge(self) -> None:
        edge = _edge("e1", "a", "b")
        assert edge.id == "e1"
        assert edge.source_node_id == "a"
        assert edge.target_node_id == "b"
        assert edge.type == WorkflowEdgeType.SEQUENTIAL

    @pytest.mark.unit
    def test_default_type(self) -> None:
        edge = WorkflowEdge(id="e1", source_node_id="a", target_node_id="b")
        assert edge.type == WorkflowEdgeType.SEQUENTIAL

    @pytest.mark.unit
    def test_default_label_none(self) -> None:
        edge = _edge("e1", "a", "b")
        assert edge.label is None

    @pytest.mark.unit
    def test_label_set(self) -> None:
        edge = WorkflowEdge(
            id="e1",
            source_node_id="a",
            target_node_id="b",
            label="true branch",
        )
        assert edge.label == "true branch"

    @pytest.mark.unit
    def test_blank_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must not be whitespace"):
            WorkflowEdge(id="  ", source_node_id="a", target_node_id="b")

    @pytest.mark.unit
    def test_frozen(self) -> None:
        edge = _edge("e1", "a", "b")
        with pytest.raises(ValidationError, match="frozen"):
            edge.label = "changed"  # type: ignore[misc]

    @pytest.mark.unit
    @pytest.mark.parametrize("edge_type", list(WorkflowEdgeType))
    def test_all_edge_types_accepted(self, edge_type: WorkflowEdgeType) -> None:
        edge = WorkflowEdge(
            id="e1",
            source_node_id="a",
            target_node_id="b",
            type=edge_type,
        )
        assert edge.type == edge_type


# ── WorkflowDefinition ─────────────────────────────────────────


class TestWorkflowDefinition:
    """WorkflowDefinition validation and constraints."""

    @pytest.mark.unit
    def test_minimal_valid(self) -> None:
        wf = _minimal_definition()
        assert wf.id == "wf-1"
        assert wf.name == "Test Workflow"
        assert wf.revision == 1
        assert wf.version == "1.0.0"
        assert len(wf.nodes) == 3
        assert len(wf.edges) == 2

    @pytest.mark.unit
    def test_default_workflow_type(self) -> None:
        wf = _minimal_definition()
        assert wf.workflow_type == WorkflowType.SEQUENTIAL_PIPELINE

    @pytest.mark.unit
    def test_custom_workflow_type(self) -> None:
        wf = _minimal_definition(workflow_type=WorkflowType.PARALLEL_EXECUTION)
        assert wf.workflow_type == WorkflowType.PARALLEL_EXECUTION

    @pytest.mark.unit
    def test_default_description(self) -> None:
        wf = _minimal_definition()
        assert wf.description == ""

    @pytest.mark.unit
    def test_revision_minimum(self) -> None:
        with pytest.raises(ValidationError, match="greater than or equal to 1"):
            _minimal_definition(revision=0)

    @pytest.mark.unit
    def test_frozen(self) -> None:
        wf = _minimal_definition()
        with pytest.raises(ValidationError, match="frozen"):
            wf.name = "changed"  # type: ignore[misc]

    @pytest.mark.unit
    def test_timestamps_auto_populated(self) -> None:
        wf = _minimal_definition()
        assert wf.created_at is not None
        assert wf.updated_at is not None

    # -- Duplicate ID rejection -----------------------------------------------

    @pytest.mark.unit
    def test_duplicate_node_ids_rejected(self) -> None:
        nodes = (
            _start_node(),
            _task_node("dup"),
            _task_node("dup"),
            _end_node(),
        )
        with pytest.raises(ValidationError, match="Duplicate node IDs"):
            _minimal_definition(
                nodes=nodes,
                edges=(
                    _edge("e1", "start-1", "dup"),
                    _edge("e2", "dup", "end-1"),
                ),
            )

    @pytest.mark.unit
    def test_duplicate_edge_ids_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Duplicate edge IDs"):
            _minimal_definition(
                edges=(
                    _edge("dup", "start-1", "task-1"),
                    _edge("dup", "task-1", "end-1"),
                ),
            )

    # -- Edge referential integrity -------------------------------------------

    @pytest.mark.unit
    def test_edge_nonexistent_source_rejected(self) -> None:
        with pytest.raises(ValidationError, match="non-existent source node"):
            _minimal_definition(
                edges=(
                    _edge("e1", "ghost", "task-1"),
                    _edge("e2", "task-1", "end-1"),
                ),
            )

    @pytest.mark.unit
    def test_edge_nonexistent_target_rejected(self) -> None:
        with pytest.raises(ValidationError, match="non-existent target node"):
            _minimal_definition(
                edges=(
                    _edge("e1", "start-1", "task-1"),
                    _edge("e2", "task-1", "ghost"),
                ),
            )

    @pytest.mark.unit
    def test_self_referencing_edge_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Self-referencing edge"):
            _minimal_definition(
                edges=(
                    _edge("e1", "task-1", "task-1"),
                    _edge("e2", "start-1", "end-1"),
                ),
            )

    # -- Terminal node constraints --------------------------------------------

    @pytest.mark.unit
    def test_no_start_node_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Expected exactly 1 START"):
            WorkflowDefinition(
                id="wf-1",
                name="No Start",
                created_by="test",
                nodes=(_task_node(), _end_node()),
                edges=(_edge("e1", "task-1", "end-1"),),
            )

    @pytest.mark.unit
    def test_no_end_node_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Expected exactly 1 END"):
            WorkflowDefinition(
                id="wf-1",
                name="No End",
                created_by="test",
                nodes=(_start_node(), _task_node()),
                edges=(_edge("e1", "start-1", "task-1"),),
            )

    @pytest.mark.unit
    def test_multiple_start_nodes_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Expected exactly 1 START"):
            WorkflowDefinition(
                id="wf-1",
                name="Two Starts",
                created_by="test",
                nodes=(
                    _start_node("s1"),
                    _start_node("s2"),
                    _end_node(),
                ),
                edges=(
                    _edge("e1", "s1", "end-1"),
                    _edge("e2", "s2", "end-1"),
                ),
            )

    @pytest.mark.unit
    def test_multiple_end_nodes_rejected(self) -> None:
        with pytest.raises(ValidationError, match="Expected exactly 1 END"):
            WorkflowDefinition(
                id="wf-1",
                name="Two Ends",
                created_by="test",
                nodes=(
                    _start_node(),
                    _end_node("e1"),
                    _end_node("e2"),
                ),
                edges=(
                    _edge("e1", "start-1", "e1"),
                    _edge("e2", "start-1", "e2"),
                ),
            )

    # -- Empty edges OK (just start + end, no connections) --------------------

    @pytest.mark.unit
    def test_empty_edges_allowed(self) -> None:
        wf = WorkflowDefinition(
            id="wf-1",
            name="Empty",
            created_by="test",
            nodes=(_start_node(), _end_node()),
            edges=(),
        )
        assert len(wf.edges) == 0

    # -- Complex graph --------------------------------------------------------

    @pytest.mark.unit
    def test_parallel_workflow(self) -> None:
        """A workflow with a parallel split and join."""
        nodes = (
            _start_node(),
            _task_node("design", "Design"),
            WorkflowNode(
                id="split-1",
                type=WorkflowNodeType.PARALLEL_SPLIT,
                label="Split",
            ),
            _task_node("frontend", "Frontend"),
            _task_node("backend", "Backend"),
            WorkflowNode(
                id="join-1",
                type=WorkflowNodeType.PARALLEL_JOIN,
                label="Join",
            ),
            _task_node("test", "Integration Test"),
            _end_node(),
        )
        edges = (
            _edge("e1", "start-1", "design"),
            _edge("e2", "design", "split-1"),
            _edge("e3", "split-1", "frontend", WorkflowEdgeType.PARALLEL_BRANCH),
            _edge("e4", "split-1", "backend", WorkflowEdgeType.PARALLEL_BRANCH),
            _edge("e5", "frontend", "join-1"),
            _edge("e6", "backend", "join-1"),
            _edge("e7", "join-1", "test"),
            _edge("e8", "test", "end-1"),
        )
        wf = WorkflowDefinition(
            id="wf-parallel",
            name="Parallel Pipeline",
            workflow_type=WorkflowType.PARALLEL_EXECUTION,
            created_by="test",
            nodes=nodes,
            edges=edges,
        )
        assert len(wf.nodes) == 8
        assert len(wf.edges) == 8

    @pytest.mark.unit
    def test_conditional_workflow(self) -> None:
        """A workflow with a conditional branch."""
        nodes = (
            _start_node(),
            _task_node("review", "Code Review"),
            WorkflowNode(
                id="cond-1",
                type=WorkflowNodeType.CONDITIONAL,
                label="Approved?",
                config={"condition_expression": "review.status == 'approved'"},
            ),
            _task_node("deploy", "Deploy"),
            _task_node("fix", "Fix Issues"),
            _end_node(),
        )
        edges = (
            _edge("e1", "start-1", "review"),
            _edge("e2", "review", "cond-1"),
            _edge("e3", "cond-1", "deploy", WorkflowEdgeType.CONDITIONAL_TRUE),
            _edge("e4", "cond-1", "fix", WorkflowEdgeType.CONDITIONAL_FALSE),
            _edge("e5", "deploy", "end-1"),
            _edge("e6", "fix", "end-1"),
        )
        wf = WorkflowDefinition(
            id="wf-cond",
            name="Conditional Pipeline",
            created_by="test",
            nodes=nodes,
            edges=edges,
        )
        assert len(wf.nodes) == 6
        assert len(wf.edges) == 6
        cond = next(n for n in wf.nodes if n.type == WorkflowNodeType.CONDITIONAL)
        assert cond.config["condition_expression"] == "review.status == 'approved'"
