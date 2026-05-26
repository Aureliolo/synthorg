"""Blueprint data models for workflow starter templates.

A ``BlueprintData`` captures a reusable workflow graph that users
can instantiate as a ``WorkflowDefinition``.  Lighter than
``WorkflowDefinition`` -- no server-generated fields (``id``,
``created_by``, timestamps).
"""

from collections import Counter
from collections.abc import Mapping  # noqa: TC003
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.enums import WorkflowEdgeType, WorkflowNodeType, WorkflowType
from synthorg.core.types import NotBlankStr  # noqa: TC001


class BlueprintNodeData(BaseModel):
    """A node in a blueprint workflow graph.

    Attributes:
        id: Unique identifier within the blueprint.
        type: Node type.
        label: Display label.
        position_x: Horizontal canvas position.
        position_y: Vertical canvas position.
        config: Type-specific configuration.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(description="Unique node identifier")
    type: WorkflowNodeType = Field(description="Node type")
    label: NotBlankStr = Field(description="Display label")
    position_x: float = Field(default=0.0, description="Canvas X position")
    position_y: float = Field(default=0.0, description="Canvas Y position")
    config: Mapping[str, object] = Field(
        default_factory=dict,
        description="Type-specific configuration",
    )


class BlueprintEdgeData(BaseModel):
    """A directed edge in a blueprint workflow graph.

    Attributes:
        id: Unique identifier within the blueprint.
        source_node_id: Source node ID.
        target_node_id: Target node ID.
        type: Edge type.
        label: Optional display label.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(description="Unique edge identifier")
    source_node_id: NotBlankStr = Field(description="Source node ID")
    target_node_id: NotBlankStr = Field(description="Target node ID")
    type: WorkflowEdgeType = Field(
        default=WorkflowEdgeType.SEQUENTIAL,
        description="Edge type",
    )
    label: NotBlankStr | None = Field(
        default=None,
        description="Optional display label",
    )


class BlueprintData(BaseModel):
    """Complete blueprint for a workflow starter template.

    Contains graph structure plus metadata.  Validated to ensure
    structural integrity (unique IDs, valid edges, exactly one START
    and one END node).

    Attributes:
        name: Machine-readable identifier (e.g. ``"feature-pipeline"``).
        display_name: Human-readable name.
        description: Detailed description of the workflow.
        workflow_type: Target execution topology.
        tags: Categorization tags for filtering.
        nodes: Nodes in the workflow graph.
        edges: Edges connecting nodes.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(description="Blueprint identifier")
    display_name: NotBlankStr = Field(description="Human-readable name")
    description: str = Field(default="", description="Detailed description")
    workflow_type: WorkflowType = Field(
        default=WorkflowType.SEQUENTIAL_PIPELINE,
        description="Target execution topology",
    )
    tags: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Categorization tags",
    )
    nodes: tuple[BlueprintNodeData, ...] = Field(
        default=(),
        description="Workflow graph nodes",
    )
    edges: tuple[BlueprintEdgeData, ...] = Field(
        default=(),
        description="Workflow graph edges",
    )

    @model_validator(mode="after")
    def _validate_unique_ids(self) -> Self:
        """Reject duplicate node or edge IDs.

        Returns:
            ``self`` unchanged when every node and edge id is unique.

        Raises:
            ValueError: When ``nodes`` or ``edges`` carry duplicate
                ids.
        """
        node_ids = tuple(n.id for n in self.nodes)
        if len(node_ids) != len(set(node_ids)):
            dupes = sorted(v for v, c in Counter(node_ids).items() if c > 1)
            msg = f"Duplicate node IDs: {dupes}"
            raise ValueError(msg)

        edge_ids = tuple(e.id for e in self.edges)
        if len(edge_ids) != len(set(edge_ids)):
            dupes = sorted(v for v, c in Counter(edge_ids).items() if c > 1)
            msg = f"Duplicate edge IDs: {dupes}"
            raise ValueError(msg)

        return self

    @model_validator(mode="after")
    def _validate_edge_references(self) -> Self:
        """Ensure edges reference existing nodes, no self-loops.

        Returns:
            ``self`` unchanged when every edge points at distinct,
            known nodes.

        Raises:
            ValueError: When an edge is self-referencing or its
                source/target id is not in the node set.
        """
        node_id_set = frozenset(n.id for n in self.nodes)
        for edge in self.edges:
            if edge.source_node_id == edge.target_node_id:
                msg = f"Self-referencing edge: {edge.id!r}"
                raise ValueError(msg)
            if edge.source_node_id not in node_id_set:
                msg = (
                    f"Edge {edge.id!r} references non-existent "
                    f"source node {edge.source_node_id!r}"
                )
                raise ValueError(msg)
            if edge.target_node_id not in node_id_set:
                msg = (
                    f"Edge {edge.id!r} references non-existent "
                    f"target node {edge.target_node_id!r}"
                )
                raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_terminal_nodes(self) -> Self:
        """Require exactly one START and one END node.

        Returns:
            ``self`` unchanged when the blueprint has exactly one
            START and one END node.

        Raises:
            ValueError: When the count of START or END nodes is not
                exactly one.
        """
        start_count = sum(1 for n in self.nodes if n.type == WorkflowNodeType.START)
        end_count = sum(1 for n in self.nodes if n.type == WorkflowNodeType.END)

        if start_count != 1:
            msg = f"Expected exactly 1 START node, found {start_count}"
            raise ValueError(msg)
        if end_count != 1:
            msg = f"Expected exactly 1 END node, found {end_count}"
            raise ValueError(msg)

        return self
