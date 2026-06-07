"""Tests for workflow definition diff computation."""

from datetime import UTC, datetime

import pytest

from synthorg.engine.workflow.definition import (
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)
from synthorg.engine.workflow.diff import (
    POSITION_CHANGE_THRESHOLD,
    EdgeChange,
    NodeChange,
    WorkflowDiff,
    compute_diff,
)
from synthorg.engine.workflow.enums import (
    WorkflowEdgeType,
    WorkflowNodeType,
    WorkflowType,
)
from synthorg.versioning import VersionSnapshot, compute_content_hash

# ── Helpers ──────────────────────────────────────────────────────

_NOW = datetime(2026, 4, 1, tzinfo=UTC)

_START = WorkflowNode(
    id="start",
    type=WorkflowNodeType.START,
    label="Start",
)
_END = WorkflowNode(
    id="end",
    type=WorkflowNodeType.END,
    label="End",
    position_x=200.0,
)
_TASK_A = WorkflowNode(
    id="task-a",
    type=WorkflowNodeType.TASK,
    label="Task A",
    position_x=100.0,
    position_y=100.0,
    config={"title": "Do A", "priority": "high"},
)
_EDGE_1 = WorkflowEdge(
    id="e1",
    source_node_id="start",
    target_node_id="end",
)


def _ver(
    version: int = 1,
    **overrides: object,
) -> VersionSnapshot[WorkflowDefinition]:
    def_defaults: dict[str, object] = {
        "id": "wfdef-test",
        "name": "Test",
        "description": "",
        "workflow_type": WorkflowType.SEQUENTIAL_PIPELINE,
        "nodes": (_START, _END),
        "edges": (_EDGE_1,),
        "created_by": "user",
        "created_at": _NOW,
        "updated_at": _NOW,
    }
    # Allow overriding definition fields via overrides; separate
    # entity_id for VersionSnapshot wrapper if provided.
    entity_id = str(overrides.pop("definition_id", "wfdef-test"))
    saved_by = str(overrides.pop("saved_by", "user"))
    # saved_at is always _NOW for tests -- pop to avoid passing
    # to WorkflowDefinition.
    overrides.pop("saved_at", None)
    def_defaults.update(overrides)
    # Override id to match entity_id
    def_defaults["id"] = entity_id
    definition = WorkflowDefinition.model_validate(def_defaults)
    return VersionSnapshot(
        entity_id=entity_id,
        version=version,
        content_hash=compute_content_hash(definition),
        snapshot=definition,
        saved_by=saved_by,
        saved_at=_NOW,
    )


# ── Cross-definition error ──────────────────────────────────────


class TestCrossDefinitionError:
    """compute_diff rejects versions from different definitions."""

    @pytest.mark.unit
    def test_different_definition_ids_raises(self) -> None:
        v1 = _ver(1, definition_id="wfdef-alpha")
        v2 = _ver(2, definition_id="wfdef-beta")
        with pytest.raises(ValueError, match="different definitions"):
            compute_diff(v1, v2)

    @pytest.mark.unit
    def test_corrupted_snapshot_id_raises(self) -> None:
        """Snapshot whose id differs from entity_id is rejected."""
        good = _ver(1, definition_id="wfdef-x")
        # Build a snapshot with mismatched snapshot.id vs entity_id.
        defn = WorkflowDefinition(
            id="wfdef-WRONG",
            name="Bad",
            description="",
            workflow_type=WorkflowType.SEQUENTIAL_PIPELINE,
            nodes=(_START, _END),
            edges=(_EDGE_1,),
            created_by="user",
        )
        corrupted = VersionSnapshot(
            entity_id="wfdef-x",
            version=2,
            content_hash=compute_content_hash(defn),
            snapshot=defn,
            saved_by="user",
            saved_at=_NOW,
        )
        with pytest.raises(ValueError, match="Corrupted"):
            compute_diff(good, corrupted)


# ── Identical versions ──────────────────────────────────────────


class TestIdenticalVersions:
    """Diffing identical content across versions produces empty diff."""

    @pytest.mark.unit
    def test_empty_diff(self) -> None:
        v1 = _ver(1)
        v2 = _ver(2)
        diff = compute_diff(v1, v2)
        assert diff.node_changes == ()
        assert diff.edge_changes == ()
        assert diff.metadata_changes == ()
        assert diff.summary == "No changes"


# ── Node changes ─────────────────────────────────────────────────


class TestNodeChanges:
    """Node-level diff detection."""

    @pytest.mark.unit
    def test_added_node(self) -> None:
        old = _ver(1)
        new = _ver(2, nodes=(_START, _END, _TASK_A))
        diff = compute_diff(old, new)

        added = [c for c in diff.node_changes if c.change_type == "added"]
        assert len(added) == 1
        assert added[0].node_id == "task-a"
        assert added[0].new_value is not None

    @pytest.mark.unit
    def test_removed_node(self) -> None:
        old = _ver(1, nodes=(_START, _END, _TASK_A))
        new = _ver(2, nodes=(_START, _END))
        diff = compute_diff(old, new)

        removed = [c for c in diff.node_changes if c.change_type == "removed"]
        assert len(removed) == 1
        assert removed[0].node_id == "task-a"
        assert removed[0].old_value is not None

    @pytest.mark.unit
    def test_moved_node(self) -> None:
        moved_task = WorkflowNode(
            id="task-a",
            type=WorkflowNodeType.TASK,
            label="Task A",
            position_x=200.0,
            position_y=300.0,
            config={"title": "Do A", "priority": "high"},
        )
        old = _ver(1, nodes=(_START, _END, _TASK_A))
        new = _ver(2, nodes=(_START, _END, moved_task))
        diff = compute_diff(old, new)

        moved = [c for c in diff.node_changes if c.change_type == "moved"]
        assert len(moved) == 1
        assert moved[0].node_id == "task-a"

    @pytest.mark.unit
    def test_move_below_threshold_ignored(self) -> None:
        tiny_move = WorkflowNode(
            id="task-a",
            type=WorkflowNodeType.TASK,
            label="Task A",
            position_x=100.0 + POSITION_CHANGE_THRESHOLD * 0.5,
            position_y=100.0 + POSITION_CHANGE_THRESHOLD * 0.5,
            config={"title": "Do A", "priority": "high"},
        )
        old = _ver(1, nodes=(_START, _END, _TASK_A))
        new = _ver(2, nodes=(_START, _END, tiny_move))
        diff = compute_diff(old, new)

        moved = [c for c in diff.node_changes if c.change_type == "moved"]
        assert len(moved) == 0

    @pytest.mark.unit
    def test_config_changed(self) -> None:
        changed = WorkflowNode(
            id="task-a",
            type=WorkflowNodeType.TASK,
            label="Task A",
            position_x=100.0,
            position_y=100.0,
            config={"title": "Do A (updated)", "priority": "low"},
        )
        old = _ver(1, nodes=(_START, _END, _TASK_A))
        new = _ver(2, nodes=(_START, _END, changed))
        diff = compute_diff(old, new)

        cfg = [c for c in diff.node_changes if c.change_type == "config_changed"]
        assert len(cfg) == 1
        assert cfg[0].node_id == "task-a"

    @pytest.mark.unit
    def test_label_changed(self) -> None:
        relabeled = WorkflowNode(
            id="task-a",
            type=WorkflowNodeType.TASK,
            label="Renamed Task",
            position_x=100.0,
            position_y=100.0,
            config={"title": "Do A", "priority": "high"},
        )
        old = _ver(1, nodes=(_START, _END, _TASK_A))
        new = _ver(2, nodes=(_START, _END, relabeled))
        diff = compute_diff(old, new)

        labels = [c for c in diff.node_changes if c.change_type == "label_changed"]
        assert len(labels) == 1

    @pytest.mark.unit
    def test_type_changed(self) -> None:
        retyped = WorkflowNode(
            id="task-a",
            type=WorkflowNodeType.CONDITIONAL,
            label="Task A",
            position_x=100.0,
            position_y=100.0,
            config={"title": "Do A", "priority": "high"},
        )
        old = _ver(1, nodes=(_START, _END, _TASK_A))
        new = _ver(2, nodes=(_START, _END, retyped))
        diff = compute_diff(old, new)

        types = [c for c in diff.node_changes if c.change_type == "type_changed"]
        assert len(types) == 1


# ── Edge changes ─────────────────────────────────────────────────


class TestEdgeChanges:
    """Edge-level diff detection."""

    @pytest.mark.unit
    def test_added_edge(self) -> None:
        new_edge = WorkflowEdge(id="e2", source_node_id="start", target_node_id="end")
        old = _ver(1)
        new = _ver(2, edges=(_EDGE_1, new_edge))
        diff = compute_diff(old, new)

        added = [c for c in diff.edge_changes if c.change_type == "added"]
        assert len(added) == 1
        assert added[0].edge_id == "e2"

    @pytest.mark.unit
    def test_removed_edge(self) -> None:
        old = _ver(1)
        new = _ver(2, edges=())
        diff = compute_diff(old, new)

        removed = [c for c in diff.edge_changes if c.change_type == "removed"]
        assert len(removed) == 1
        assert removed[0].edge_id == "e1"

    @pytest.mark.unit
    def test_reconnected_edge(self) -> None:
        reconnected = WorkflowEdge(
            id="e1", source_node_id="end", target_node_id="start"
        )
        nodes = (_START, _END)
        old = _ver(1, nodes=nodes)
        new = _ver(2, nodes=nodes, edges=(reconnected,))
        diff = compute_diff(old, new)

        recon = [c for c in diff.edge_changes if c.change_type == "reconnected"]
        assert len(recon) == 1

    @pytest.mark.unit
    def test_edge_label_changed(self) -> None:
        """Detect edge label changes."""
        old_edge = WorkflowEdge(
            id="e1",
            source_node_id="start",
            target_node_id="end",
            label="Old Label",
        )
        new_edge = WorkflowEdge(
            id="e1",
            source_node_id="start",
            target_node_id="end",
            label="New Label",
        )
        old = _ver(1, edges=(old_edge,))
        new = _ver(2, edges=(new_edge,))
        diff = compute_diff(old, new)

        label_changes = [
            c for c in diff.edge_changes if c.change_type == "label_changed"
        ]
        assert len(label_changes) == 1
        assert label_changes[0].old_value == {"label": "Old Label"}
        assert label_changes[0].new_value == {"label": "New Label"}

    @pytest.mark.unit
    def test_edge_type_changed(self) -> None:
        retyped = WorkflowEdge(
            id="e1",
            source_node_id="start",
            target_node_id="end",
            type=WorkflowEdgeType.CONDITIONAL_TRUE,
        )
        old = _ver(1)
        new = _ver(2, edges=(retyped,))
        diff = compute_diff(old, new)

        types = [c for c in diff.edge_changes if c.change_type == "type_changed"]
        assert len(types) == 1


# ── Metadata changes ─────────────────────────────────────────────


class TestMetadataChanges:
    """Metadata-level diff detection."""

    @pytest.mark.unit
    def test_name_changed(self) -> None:
        old = _ver(1, name="Old Name")
        new = _ver(2, name="New Name")
        diff = compute_diff(old, new)

        meta = [m for m in diff.metadata_changes if m.field == "name"]
        assert len(meta) == 1
        assert meta[0].old_value == "Old Name"
        assert meta[0].new_value == "New Name"

    @pytest.mark.unit
    def test_description_changed(self) -> None:
        old = _ver(1, description="Old desc")
        new = _ver(2, description="New desc")
        diff = compute_diff(old, new)

        meta = [m for m in diff.metadata_changes if m.field == "description"]
        assert len(meta) == 1

    @pytest.mark.unit
    def test_workflow_type_changed(self) -> None:
        old = _ver(1, workflow_type=WorkflowType.SEQUENTIAL_PIPELINE)
        new = _ver(2, workflow_type=WorkflowType.PARALLEL_EXECUTION)
        diff = compute_diff(old, new)

        meta = [m for m in diff.metadata_changes if m.field == "workflow_type"]
        assert len(meta) == 1


# ── Summary ──────────────────────────────────────────────────────


class TestSummary:
    """Human-readable summary generation."""

    @pytest.mark.unit
    def test_combined_summary(self) -> None:
        old = _ver(1, nodes=(_START, _END, _TASK_A), name="Old")
        new = _ver(2, nodes=(_START, _END), name="New")
        diff = compute_diff(old, new)

        assert "1 removed node" in diff.summary
        assert "metadata changed: name" in diff.summary

    @pytest.mark.unit
    def test_no_changes_summary(self) -> None:
        v1 = _ver(1)
        v2 = _ver(2)
        diff = compute_diff(v1, v2)
        assert diff.summary == "No changes"


# ── Model validator tests ────────────────────────────────────────


class TestNodeChangeValidation:
    """NodeChange._validate_values enforcement."""

    @pytest.mark.unit
    def test_added_with_old_value_raises(self) -> None:
        with pytest.raises(ValueError, match="added"):
            NodeChange(
                node_id="n1",
                change_type="added",
                old_value={"x": 1},
                new_value={"x": 2},
            )

    @pytest.mark.unit
    def test_removed_with_new_value_raises(self) -> None:
        with pytest.raises(ValueError, match="removed"):
            NodeChange(
                node_id="n1",
                change_type="removed",
                old_value={"x": 1},
                new_value={"x": 2},
            )

    @pytest.mark.unit
    def test_moved_missing_old_value_raises(self) -> None:
        with pytest.raises(ValueError, match="moved"):
            NodeChange(
                node_id="n1",
                change_type="moved",
                new_value={"position_x": 10.0},
            )

    @pytest.mark.unit
    def test_moved_missing_new_value_raises(self) -> None:
        with pytest.raises(ValueError, match="moved"):
            NodeChange(
                node_id="n1",
                change_type="moved",
                old_value={"position_x": 5.0},
            )


class TestEdgeChangeValidation:
    """EdgeChange._validate_values enforcement."""

    @pytest.mark.unit
    def test_added_with_old_value_raises(self) -> None:
        with pytest.raises(ValueError, match="added"):
            EdgeChange(
                edge_id="e1",
                change_type="added",
                old_value={"source_node_id": "a"},
                new_value={"source_node_id": "b"},
            )

    @pytest.mark.unit
    def test_removed_with_new_value_raises(self) -> None:
        with pytest.raises(ValueError, match="removed"):
            EdgeChange(
                edge_id="e1",
                change_type="removed",
                old_value={"source_node_id": "a"},
                new_value={"source_node_id": "b"},
            )

    @pytest.mark.unit
    def test_reconnected_missing_old_value_raises(self) -> None:
        with pytest.raises(ValueError, match="reconnected"):
            EdgeChange(
                edge_id="e1",
                change_type="reconnected",
                new_value={"source_node_id": "b"},
            )

    @pytest.mark.unit
    def test_reconnected_missing_new_value_raises(self) -> None:
        with pytest.raises(ValueError, match="reconnected"):
            EdgeChange(
                edge_id="e1",
                change_type="reconnected",
                old_value={"source_node_id": "a"},
            )


class TestWorkflowDiffVersionRangeValidation:
    """WorkflowDiff._validate_version_range enforcement."""

    @pytest.mark.unit
    def test_same_from_to_version_raises(self) -> None:
        with pytest.raises(ValueError, match="from_version must be less"):
            WorkflowDiff(
                definition_id="wfdef-test",
                from_version=3,
                to_version=3,
            )

    @pytest.mark.unit
    def test_from_greater_than_to_raises(self) -> None:
        with pytest.raises(ValueError, match="from_version must be less"):
            WorkflowDiff(
                definition_id="wfdef-test",
                from_version=5,
                to_version=2,
            )
