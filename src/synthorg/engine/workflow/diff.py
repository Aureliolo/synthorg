"""Workflow definition diff computation.

Pure functions for computing structural differences between two
workflow definition versions (node changes, edge changes, metadata
changes).
"""

from collections import Counter
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.definition import (
    WorkflowDefinition,
    WorkflowEdge,
    WorkflowNode,
)
from synthorg.observability import get_logger
from synthorg.observability.events.workflow_definition import (
    WORKFLOW_DEF_INVALID_REQUEST,
)
from synthorg.versioning.models import VersionSnapshot

logger = get_logger(__name__)

#: Position changes below this threshold (pixels) are ignored.
POSITION_CHANGE_THRESHOLD: float = 1.0


def _validate_change_values(
    change_type: str,
    old_value: dict[str, object] | None,
    new_value: dict[str, object] | None,
) -> None:
    """Validate old_value/new_value presence rules per change_type.

    Raises:
        ValueError: When the presence of ``old_value`` / ``new_value``
            does not match the rules for ``change_type`` (added needs
            only ``new_value``; removed needs only ``old_value``; all
            other change types need both).
    """
    if change_type == "added":
        if old_value is not None or new_value is None:
            msg = "added change must have new_value only"
            raise ValueError(msg)
    elif change_type == "removed":
        if old_value is None or new_value is not None:
            msg = "removed change must have old_value only"
            raise ValueError(msg)
    elif old_value is None or new_value is None:
        msg = f"{change_type} change must have both old_value and new_value"
        raise ValueError(msg)


class NodeChange(BaseModel):
    """A single change to a workflow node between two versions.

    Attributes:
        node_id: The node affected.
        change_type: Kind of change detected.
        old_value: Previous state (None for added nodes).
        new_value: New state (None for removed nodes).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    node_id: NotBlankStr
    change_type: Literal[
        "added",
        "removed",
        "moved",
        "config_changed",
        "label_changed",
        "type_changed",
    ]
    old_value: dict[str, object] | None = None
    new_value: dict[str, object] | None = None

    @model_validator(mode="after")
    def _validate_values(self) -> Self:
        """Enforce old_value/new_value presence rules per change_type.

        Returns:
            ``self`` unchanged when the change values are consistent.

        Raises:
            ValueError: Propagated from :func:`_validate_change_values`
                when the value presence rules are violated.
        """
        _validate_change_values(
            self.change_type,
            self.old_value,
            self.new_value,
        )
        return self


class EdgeChange(BaseModel):
    """A single change to a workflow edge between two versions.

    Attributes:
        edge_id: The edge affected.
        change_type: Kind of change detected.
        old_value: Previous state (None for added edges).
        new_value: New state (None for removed edges).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    edge_id: NotBlankStr
    change_type: Literal[
        "added",
        "removed",
        "reconnected",
        "type_changed",
        "label_changed",
    ]
    old_value: dict[str, object] | None = None
    new_value: dict[str, object] | None = None

    @model_validator(mode="after")
    def _validate_values(self) -> Self:
        """Enforce old_value/new_value presence rules per change_type.

        Returns:
            ``self`` unchanged when the change values are consistent.

        Raises:
            ValueError: Propagated from :func:`_validate_change_values`
                when the value presence rules are violated.
        """
        _validate_change_values(
            self.change_type,
            self.old_value,
            self.new_value,
        )
        return self


class MetadataChange(BaseModel):
    """A metadata field change between two versions.

    Attributes:
        field: Name of the changed field.
        old_value: Previous value.
        new_value: New value.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    field: NotBlankStr
    old_value: str
    new_value: str


class WorkflowDiff(BaseModel):
    """Complete diff result between two workflow definition versions.

    Attributes:
        definition_id: The workflow definition ID.
        from_version: Source version number.
        to_version: Target version number.
        node_changes: All detected node changes.
        edge_changes: All detected edge changes.
        metadata_changes: Metadata field changes.
        summary: Human-readable summary string.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    definition_id: NotBlankStr
    from_version: int = Field(ge=1)
    to_version: int = Field(ge=1)
    node_changes: tuple[NodeChange, ...] = ()
    edge_changes: tuple[EdgeChange, ...] = ()
    metadata_changes: tuple[MetadataChange, ...] = ()
    summary: str = ""

    @model_validator(mode="after")
    def _validate_version_range(self) -> Self:
        """Reject diffs where source version is not less than target.

        Returns:
            ``self`` unchanged when ``from_version < to_version``.

        Raises:
            ValueError: When ``from_version >= to_version``.
        """
        if self.from_version >= self.to_version:
            msg = "from_version must be less than to_version"
            raise ValueError(msg)
        return self


def compute_diff(
    old: VersionSnapshot[WorkflowDefinition],
    new: VersionSnapshot[WorkflowDefinition],
) -> WorkflowDiff:
    """Compute the structural diff between two version snapshots.

    Both snapshots must reference the same workflow definition
    (matching ``entity_id``).

    Args:
        old: The earlier version snapshot.
        new: The later version snapshot.

    Returns:
        A :class:`WorkflowDiff` describing all changes.

    Raises:
        ValueError: If the two versions belong to different definitions.
    """
    if old.entity_id != new.entity_id:
        msg = "Cannot diff versions from different definitions"
        logger.warning(
            WORKFLOW_DEF_INVALID_REQUEST,
            old_definition_id=old.entity_id,
            new_definition_id=new.entity_id,
            reason=msg,
        )
        raise ValueError(msg)
    for label, snap in (("old", old), ("new", new)):
        if snap.snapshot.id != snap.entity_id:
            msg = (
                f"Corrupted {label} snapshot: snapshot.id "
                f"{snap.snapshot.id!r} != entity_id {snap.entity_id!r}"
            )
            logger.warning(
                WORKFLOW_DEF_INVALID_REQUEST,
                snapshot_id=snap.snapshot.id,
                entity_id=snap.entity_id,
                reason=msg,
            )
            raise ValueError(msg)

    node_changes = _diff_nodes(old.snapshot, new.snapshot)
    edge_changes = _diff_edges(old.snapshot, new.snapshot)
    metadata_changes = _diff_metadata(old.snapshot, new.snapshot)
    summary = _build_summary(node_changes, edge_changes, metadata_changes)

    return WorkflowDiff(
        definition_id=old.entity_id,
        from_version=old.version,
        to_version=new.version,
        node_changes=tuple(node_changes),
        edge_changes=tuple(edge_changes),
        metadata_changes=tuple(metadata_changes),
        summary=summary,
    )


def _compare_matched_node(
    node_id: str,
    old_node: WorkflowNode,
    new_node: WorkflowNode,
) -> list[NodeChange]:
    """Compare a single matched node pair and return all changes.

    Args:
        node_id: The shared node ID.
        old_node: The node from the earlier version.
        new_node: The node from the later version.

    Returns:
        A list of ``NodeChange`` items for every detected difference.
    """
    changes: list[NodeChange] = []

    if old_node.type != new_node.type:
        changes.append(
            NodeChange(
                node_id=node_id,
                change_type="type_changed",
                old_value={"type": old_node.type.value},
                new_value={"type": new_node.type.value},
            )
        )

    if old_node.label != new_node.label:
        changes.append(
            NodeChange(
                node_id=node_id,
                change_type="label_changed",
                old_value={"label": old_node.label},
                new_value={"label": new_node.label},
            )
        )

    dx = abs(old_node.position_x - new_node.position_x)
    dy = abs(old_node.position_y - new_node.position_y)
    if dx > POSITION_CHANGE_THRESHOLD or dy > POSITION_CHANGE_THRESHOLD:
        changes.append(
            NodeChange(
                node_id=node_id,
                change_type="moved",
                old_value={
                    "position_x": old_node.position_x,
                    "position_y": old_node.position_y,
                },
                new_value={
                    "position_x": new_node.position_x,
                    "position_y": new_node.position_y,
                },
            )
        )

    old_cfg = dict(old_node.config) if old_node.config else {}
    new_cfg = dict(new_node.config) if new_node.config else {}
    if old_cfg != new_cfg:
        changes.append(
            NodeChange(
                node_id=node_id,
                change_type="config_changed",
                old_value={"config": old_cfg},
                new_value={"config": new_cfg},
            )
        )

    return changes


def _diff_nodes(
    old: WorkflowDefinition,
    new: WorkflowDefinition,
) -> list[NodeChange]:
    """Compare nodes between two versions.

    Returns:
        List of :class:`NodeChange` records covering added, removed,
        and modified nodes (move, type, label, config changes).
    """
    old_map = {n.id: n for n in old.nodes}
    new_map = {n.id: n for n in new.nodes}
    changes: list[NodeChange] = []

    # Added nodes.
    for nid in sorted(new_map.keys() - old_map.keys()):
        n = new_map[nid]
        changes.append(
            NodeChange(
                node_id=nid,
                change_type="added",
                new_value=n.model_dump(mode="json"),
            )
        )

    # Removed nodes.
    for nid in sorted(old_map.keys() - new_map.keys()):
        n = old_map[nid]
        changes.append(
            NodeChange(
                node_id=nid,
                change_type="removed",
                old_value=n.model_dump(mode="json"),
            )
        )

    # Matched nodes -- check for modifications.
    for nid in sorted(old_map.keys() & new_map.keys()):
        changes.extend(
            _compare_matched_node(nid, old_map[nid], new_map[nid]),
        )

    return changes


def _compare_matched_edge(
    edge_id: str,
    old_edge: WorkflowEdge,
    new_edge: WorkflowEdge,
) -> list[EdgeChange]:
    """Compare a single matched edge pair and return all changes.

    Args:
        edge_id: The shared edge ID.
        old_edge: The edge from the earlier version.
        new_edge: The edge from the later version.

    Returns:
        A list of ``EdgeChange`` items for every detected difference.
    """
    changes: list[EdgeChange] = []

    if (
        old_edge.source_node_id != new_edge.source_node_id
        or old_edge.target_node_id != new_edge.target_node_id
    ):
        changes.append(
            EdgeChange(
                edge_id=edge_id,
                change_type="reconnected",
                old_value={
                    "source_node_id": old_edge.source_node_id,
                    "target_node_id": old_edge.target_node_id,
                },
                new_value={
                    "source_node_id": new_edge.source_node_id,
                    "target_node_id": new_edge.target_node_id,
                },
            )
        )

    if old_edge.type != new_edge.type:
        changes.append(
            EdgeChange(
                edge_id=edge_id,
                change_type="type_changed",
                old_value={"type": old_edge.type.value},
                new_value={"type": new_edge.type.value},
            )
        )

    if old_edge.label != new_edge.label:
        changes.append(
            EdgeChange(
                edge_id=edge_id,
                change_type="label_changed",
                old_value={"label": old_edge.label},
                new_value={"label": new_edge.label},
            )
        )

    return changes


def _diff_edges(
    old: WorkflowDefinition,
    new: WorkflowDefinition,
) -> list[EdgeChange]:
    """Compare edges between two versions.

    Returns:
        List of :class:`EdgeChange` records covering added, removed,
        and modified edges (reconnect, type, label changes).
    """
    old_map = {e.id: e for e in old.edges}
    new_map = {e.id: e for e in new.edges}
    changes: list[EdgeChange] = []

    # Added edges.
    for eid in sorted(new_map.keys() - old_map.keys()):
        e = new_map[eid]
        changes.append(
            EdgeChange(
                edge_id=eid,
                change_type="added",
                new_value=e.model_dump(mode="json"),
            )
        )

    # Removed edges.
    for eid in sorted(old_map.keys() - new_map.keys()):
        e = old_map[eid]
        changes.append(
            EdgeChange(
                edge_id=eid,
                change_type="removed",
                old_value=e.model_dump(mode="json"),
            )
        )

    # Matched edges -- check for modifications.
    for eid in sorted(old_map.keys() & new_map.keys()):
        changes.extend(
            _compare_matched_edge(eid, old_map[eid], new_map[eid]),
        )

    return changes


def _diff_metadata(
    old: WorkflowDefinition,
    new: WorkflowDefinition,
) -> list[MetadataChange]:
    """Compare metadata fields between two versions.

    Returns:
        List of :class:`MetadataChange` records, one per differing
        metadata field (name, description, workflow_type).
    """
    changes: list[MetadataChange] = []

    if old.name != new.name:
        changes.append(
            MetadataChange(
                field="name",
                old_value=old.name,
                new_value=new.name,
            )
        )

    if old.description != new.description:
        changes.append(
            MetadataChange(
                field="description",
                old_value=old.description,
                new_value=new.description,
            )
        )

    if old.workflow_type != new.workflow_type:
        changes.append(
            MetadataChange(
                field="workflow_type",
                old_value=old.workflow_type.value,
                new_value=new.workflow_type.value,
            )
        )

    return changes


def _build_summary(
    node_changes: list[NodeChange],
    edge_changes: list[EdgeChange],
    metadata_changes: list[MetadataChange],
) -> str:
    """Build a human-readable summary from change lists.

    Returns:
        A short text summary aggregating change counts per category.
    """
    parts: list[str] = []

    # Count node changes by type.
    node_counts: dict[str, int] = dict(
        Counter(nc.change_type for nc in node_changes),
    )
    _node_ct_order = (
        "added",
        "removed",
        "moved",
        "config_changed",
        "label_changed",
        "type_changed",
    )
    for ct in _node_ct_order:
        count = node_counts.get(ct, 0)
        if count > 0:
            label = ct.replace("_", " ")
            noun = "node" if count == 1 else "nodes"
            parts.append(f"{count} {label} {noun}")

    # Count edge changes by type.
    edge_counts: dict[str, int] = dict(
        Counter(ec.change_type for ec in edge_changes),
    )
    for ct in ("added", "removed", "reconnected", "type_changed", "label_changed"):
        count = edge_counts.get(ct, 0)
        if count > 0:
            label = ct.replace("_", " ")
            noun = "edge" if count == 1 else "edges"
            parts.append(f"{count} {label} {noun}")

    if metadata_changes:
        fields = ", ".join(mc.field for mc in metadata_changes)
        parts.append(f"metadata changed: {fields}")

    if not parts:
        return "No changes"

    return "; ".join(parts)
