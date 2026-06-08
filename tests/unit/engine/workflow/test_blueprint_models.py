"""Tests for workflow blueprint data models."""

import pytest
from pydantic import ValidationError

from synthorg.engine.workflow.blueprint_models import (
    BlueprintData,
    BlueprintEdgeData,
    BlueprintNodeData,
)
from synthorg.engine.workflow.enums import (
    WorkflowEdgeType,
    WorkflowNodeType,
    WorkflowType,
)

# ── Helpers ──────────────────────────────────────────────────────


def _start_node(node_id: str = "start") -> BlueprintNodeData:
    return BlueprintNodeData(id=node_id, type=WorkflowNodeType.START, label="Start")


def _end_node(node_id: str = "end") -> BlueprintNodeData:
    return BlueprintNodeData(id=node_id, type=WorkflowNodeType.END, label="End")


def _task_node(
    node_id: str = "task-1",
    label: str = "Do work",
) -> BlueprintNodeData:
    return BlueprintNodeData(
        id=node_id,
        type=WorkflowNodeType.TASK,
        label=label,
        position_x=200.0,
        position_y=100.0,
        config={"title": "Test Task", "task_type": "development"},
    )


def _edge(
    edge_id: str,
    source: str,
    target: str,
    edge_type: WorkflowEdgeType = WorkflowEdgeType.SEQUENTIAL,
) -> BlueprintEdgeData:
    return BlueprintEdgeData(
        id=edge_id,
        source_node_id=source,
        target_node_id=target,
        type=edge_type,
    )


def _minimal_blueprint(**overrides: object) -> BlueprintData:
    """Build a minimal valid blueprint: start -> task -> end."""
    defaults: dict[str, object] = {
        "name": "test-bp",
        "display_name": "Test Blueprint",
        "description": "A test blueprint",
        "workflow_type": WorkflowType.SEQUENTIAL_PIPELINE,
        "nodes": (_start_node(), _task_node(), _end_node()),
        "edges": (_edge("e1", "start", "task-1"), _edge("e2", "task-1", "end")),
    }
    defaults.update(overrides)
    return BlueprintData.model_validate(defaults)


# ── BlueprintNodeData ────────────────────────────────────────────


class TestBlueprintNodeData:
    """BlueprintNodeData validation."""

    @pytest.mark.unit
    def test_valid_node(self) -> None:
        node = _task_node()
        assert node.id == "task-1"
        assert node.type == WorkflowNodeType.TASK
        assert node.label == "Do work"
        assert node.position_x == 200.0
        assert node.position_y == 100.0

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
        with pytest.raises(
            ValidationError, match="String should have at least 1 character"
        ):
            BlueprintNodeData(id="", type=WorkflowNodeType.TASK, label="X")

    @pytest.mark.unit
    def test_whitespace_id_rejected(self) -> None:
        with pytest.raises(ValidationError, match="whitespace"):
            BlueprintNodeData(id="  ", type=WorkflowNodeType.TASK, label="X")

    @pytest.mark.unit
    def test_blank_label_rejected(self) -> None:
        with pytest.raises(
            ValidationError, match="String should have at least 1 character"
        ):
            BlueprintNodeData(id="n1", type=WorkflowNodeType.TASK, label="")

    @pytest.mark.unit
    def test_frozen(self) -> None:
        node = _task_node()
        with pytest.raises(ValidationError, match="frozen"):
            node.id = "changed"  # type: ignore[misc]


# ── BlueprintEdgeData ────────────────────────────────────────────


class TestBlueprintEdgeData:
    """BlueprintEdgeData validation."""

    @pytest.mark.unit
    def test_valid_edge(self) -> None:
        edge = _edge("e1", "a", "b")
        assert edge.id == "e1"
        assert edge.source_node_id == "a"
        assert edge.target_node_id == "b"
        assert edge.type == WorkflowEdgeType.SEQUENTIAL

    @pytest.mark.unit
    def test_default_type(self) -> None:
        edge = BlueprintEdgeData(id="e1", source_node_id="a", target_node_id="b")
        assert edge.type == WorkflowEdgeType.SEQUENTIAL

    @pytest.mark.unit
    def test_optional_label_defaults_none(self) -> None:
        edge = _edge("e1", "a", "b")
        assert edge.label is None

    @pytest.mark.unit
    def test_frozen(self) -> None:
        edge = _edge("e1", "a", "b")
        with pytest.raises(ValidationError, match="frozen"):
            edge.id = "changed"  # type: ignore[misc]


# ── BlueprintData ────────────────────────────────────────────────


class TestBlueprintData:
    """BlueprintData validation and constraints."""

    @pytest.mark.unit
    def test_valid_blueprint(self) -> None:
        bp = _minimal_blueprint()
        assert bp.name == "test-bp"
        assert bp.display_name == "Test Blueprint"
        assert bp.workflow_type == WorkflowType.SEQUENTIAL_PIPELINE
        assert len(bp.nodes) == 3
        assert len(bp.edges) == 2

    @pytest.mark.unit
    def test_requires_exactly_one_start_node(self) -> None:
        with pytest.raises(ValidationError, match="Expected exactly 1 START"):
            _minimal_blueprint(
                nodes=(_task_node(), _end_node()),
                edges=(_edge("e1", "task-1", "end"),),
            )

    @pytest.mark.unit
    def test_rejects_two_start_nodes(self) -> None:
        with pytest.raises(ValidationError, match="Expected exactly 1 START"):
            _minimal_blueprint(
                nodes=(
                    _start_node("s1"),
                    _start_node("s2"),
                    _task_node(),
                    _end_node(),
                ),
                edges=(
                    _edge("e1", "s1", "task-1"),
                    _edge("e2", "task-1", "end"),
                ),
            )

    @pytest.mark.unit
    def test_requires_exactly_one_end_node(self) -> None:
        with pytest.raises(ValidationError, match="Expected exactly 1 END"):
            _minimal_blueprint(
                nodes=(_start_node(), _task_node()),
                edges=(_edge("e1", "start", "task-1"),),
            )

    @pytest.mark.unit
    def test_rejects_duplicate_node_ids(self) -> None:
        with pytest.raises(ValidationError, match="Duplicate node IDs"):
            _minimal_blueprint(
                nodes=(
                    _start_node(),
                    _task_node("task-1"),
                    _task_node("task-1"),
                    _end_node(),
                ),
                edges=(
                    _edge("e1", "start", "task-1"),
                    _edge("e2", "task-1", "end"),
                ),
            )

    @pytest.mark.unit
    def test_rejects_duplicate_edge_ids(self) -> None:
        with pytest.raises(ValidationError, match="Duplicate edge IDs"):
            _minimal_blueprint(
                edges=(
                    _edge("e1", "start", "task-1"),
                    _edge("e1", "task-1", "end"),
                ),
            )

    @pytest.mark.unit
    def test_rejects_edge_to_nonexistent_source(self) -> None:
        with pytest.raises(ValidationError, match="non-existent source"):
            _minimal_blueprint(
                edges=(
                    _edge("e1", "ghost", "task-1"),
                    _edge("e2", "task-1", "end"),
                ),
            )

    @pytest.mark.unit
    def test_rejects_edge_to_nonexistent_target(self) -> None:
        with pytest.raises(ValidationError, match="non-existent target"):
            _minimal_blueprint(
                edges=(
                    _edge("e1", "start", "ghost"),
                    _edge("e2", "task-1", "end"),
                ),
            )

    @pytest.mark.unit
    def test_rejects_self_referencing_edge(self) -> None:
        with pytest.raises(ValidationError, match="Self-referencing"):
            _minimal_blueprint(
                edges=(
                    _edge("e1", "start", "task-1"),
                    _edge("e2", "task-1", "task-1"),
                ),
            )

    @pytest.mark.unit
    def test_empty_tags_allowed(self) -> None:
        bp = _minimal_blueprint(tags=())
        assert bp.tags == ()

    @pytest.mark.unit
    def test_frozen(self) -> None:
        bp = _minimal_blueprint()
        with pytest.raises(ValidationError, match="frozen"):
            bp.name = "changed"  # type: ignore[misc]

    @pytest.mark.unit
    def test_blank_name_rejected(self) -> None:
        with pytest.raises(
            ValidationError, match="String should have at least 1 character"
        ):
            _minimal_blueprint(name="")

    @pytest.mark.unit
    def test_blank_display_name_rejected(self) -> None:
        with pytest.raises(
            ValidationError, match="String should have at least 1 character"
        ):
            _minimal_blueprint(display_name="")
