"""Visual workflow definition models.

A ``WorkflowDefinition`` is a design-time blueprint -- a saveable
directed graph of workflow nodes and edges that can be validated and
exported as YAML for the engine's coordination/decomposition system.

This is distinct from ``WorkflowConfig`` (runtime operational config
for Kanban/Sprint settings).
"""

import json
import math
import re
from collections import Counter
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from synthorg.core.iso_datetime import is_valid_iso_datetime
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.enums import (
    WorkflowEdgeType,
    WorkflowNodeType,
    WorkflowType,
    WorkflowValueType,
)

_DEFAULT_SEMVER = "1.0.0"

_VALUE_TYPE_CHECKS: dict[WorkflowValueType, type | tuple[type, ...]] = {
    WorkflowValueType.STRING: str,
    WorkflowValueType.INTEGER: int,
    WorkflowValueType.FLOAT: (int, float),
    WorkflowValueType.BOOLEAN: bool,
    WorkflowValueType.DATETIME: (datetime, str),
    WorkflowValueType.TASK_REF: str,
    WorkflowValueType.AGENT_REF: str,
}


def _check_default_type(name: str, default: object, vtype: WorkflowValueType) -> None:
    """Validate that *default* is compatible with *vtype*.

    Raises:
        ValueError: If ``default`` is not serialisable as JSON when
            ``vtype`` is ``JSON``, if ``default``'s Python type does
            not match ``vtype``, if a ``DATETIME`` default is not a
            valid ISO-8601 string, or if a ``FLOAT`` default is not
            finite. Raised as ``ValueError`` (not ``TypeError``) so the
            calling ``model_validator`` wraps it into a ``ValidationError``.
    """
    if vtype is WorkflowValueType.JSON:
        try:
            json.dumps(default, allow_nan=False)
        except (TypeError, ValueError) as exc:
            msg = f"Declaration {name!r}: JSON default is not serializable"
            raise ValueError(msg) from exc
        return
    expected = _VALUE_TYPE_CHECKS.get(vtype)
    if expected is None:
        return
    if vtype in (WorkflowValueType.INTEGER, WorkflowValueType.FLOAT) and isinstance(
        default, bool
    ):
        msg = f"Declaration {name!r}: default must be {vtype.value}, got bool"
        raise ValueError(msg)
    if not isinstance(default, expected):
        msg = (
            f"Declaration {name!r}: default must be"
            f" {vtype.value}, got {type(default).__name__}"
        )
        raise ValueError(msg)  # noqa: TRY004 -- Pydantic needs ValueError
    if (
        vtype is WorkflowValueType.DATETIME
        and isinstance(default, str)
        and not is_valid_iso_datetime(default)
    ):
        msg = f"Declaration {name!r}: DATETIME default is not valid ISO-8601"
        raise ValueError(msg)
    if (
        vtype is WorkflowValueType.FLOAT
        and isinstance(default, (int, float))
        and not math.isfinite(default)
    ):
        msg = f"Declaration {name!r}: FLOAT default must be finite"
        raise ValueError(msg)


class WorkflowIODeclaration(BaseModel):
    """A typed input or output declaration for a workflow definition.

    Subworkflows use these declarations as their contract: parents must
    provide matching inputs, and the parent scope receives outputs
    projected from the child frame's variables.

    Attributes:
        name: Identifier used by parent bindings and child expressions.
        type: The value kind (see :class:`WorkflowValueType`).
        required: Whether the caller must provide this input.
            Always ``True`` for outputs.
        default: Optional default value when ``required`` is ``False``.
        description: Free-text description for the UI.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(description="Identifier")
    type: WorkflowValueType = Field(description="Typed value kind")
    required: bool = Field(default=True, description="Whether a value is mandatory")
    default: object | None = Field(
        default=None,
        description="Default value when not required",
    )
    description: str = Field(default="", description="Human-readable description")

    @model_validator(mode="after")
    def _validate_default_compatible(self) -> Self:
        """Reject defaults on required declarations and type-check defaults.

        Returns:
            The unmodified ``self`` (validators must return the model).

        Raises:
            ValueError: If a required declaration carries a default,
                or if :func:`_check_default_type` re-raises a typed
                rejection that surfaces here as a ``ValueError``.
        """
        if self.required and self.default is not None:
            msg = (
                f"Declaration {self.name!r}: required declarations "
                f"must not carry a default value"
            )
            raise ValueError(msg)
        if self.default is not None:
            _check_default_type(self.name, self.default, self.type)
        return self


class WorkflowNode(BaseModel):
    """A single node in a visual workflow graph.

    Attributes:
        id: Unique identifier within the workflow definition.
        type: Node type (task, conditional, parallel split/join, etc.).
        label: Display label for the node.
        position_x: Horizontal position on the visual canvas.
        position_y: Vertical position on the visual canvas.
        config: Type-specific configuration (task type, priority,
            agent role, condition expression, etc.).
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


class WorkflowEdge(BaseModel):
    """A directed edge connecting two workflow nodes.

    Attributes:
        id: Unique identifier within the workflow definition.
        source_node_id: ID of the source node.
        target_node_id: ID of the target node.
        type: Edge type (sequential, conditional branch, parallel).
        label: Optional display label (e.g. condition text).
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


class WorkflowDefinition(BaseModel):
    """A complete visual workflow definition.

    Contains the full graph (nodes + edges) plus metadata for
    persistence and concurrency control.

    Attributes:
        id: Server-generated unique identifier.
        name: Human-readable workflow name.
        description: Optional detailed description.
        workflow_type: The execution topology this workflow targets.
        version: Semver string (``MAJOR.MINOR.PATCH``) identifying this
            revision for subworkflow pinning and publication.
        inputs: Typed input contract (used when this definition is
            referenced as a subworkflow).
        outputs: Typed output contract (used when this definition is
            referenced as a subworkflow).
        is_subworkflow: Whether this definition is published to the
            subworkflow registry.
        nodes: All nodes in the workflow graph.
        edges: All edges connecting the nodes.
        created_by: Identity of the creator.
        created_at: Creation timestamp (UTC).
        updated_at: Last update timestamp (UTC).
        revision: Optimistic concurrency counter (monotonic integer).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: UUID = Field(
        default_factory=uuid4,
        description="Unique workflow definition ID",
    )
    name: NotBlankStr = Field(description="Workflow name")
    description: str = Field(default="", description="Detailed description")
    workflow_type: WorkflowType = Field(
        default=WorkflowType.SEQUENTIAL_PIPELINE,
        description="Target execution topology",
    )
    version: NotBlankStr = Field(
        default=_DEFAULT_SEMVER,
        description="Semver version string (MAJOR.MINOR.PATCH)",
    )
    inputs: tuple[WorkflowIODeclaration, ...] = Field(
        default=(),
        description="Typed input contract (subworkflow-facing)",
    )
    outputs: tuple[WorkflowIODeclaration, ...] = Field(
        default=(),
        description="Typed output contract (subworkflow-facing)",
    )
    is_subworkflow: bool = Field(
        default=False,
        description="Whether this definition is published to the registry",
    )
    nodes: tuple[WorkflowNode, ...] = Field(
        default=(),
        description="Nodes in the workflow graph",
    )
    edges: tuple[WorkflowEdge, ...] = Field(
        default=(),
        description="Edges connecting nodes",
    )
    created_by: NotBlankStr = Field(description="Creator identity")
    created_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Creation timestamp (UTC)",
    )
    updated_at: datetime = Field(
        default_factory=lambda: datetime.now(UTC),
        description="Last update timestamp (UTC)",
    )
    revision: int = Field(
        default=1,
        ge=1,
        description="Optimistic concurrency counter",
    )

    @field_validator("version")
    @classmethod
    def _validate_semver(cls, value: str) -> str:
        """Reject non-strict semver (must be MAJOR.MINOR.PATCH).

        Returns:
            The validated ``value`` unchanged.

        Raises:
            ValueError: If ``value`` is not strict ``MAJOR.MINOR.PATCH``
                semver.
        """
        if not re.fullmatch(r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)", value):
            msg = (
                f"Invalid version {value!r}: must be strict"
                f" MAJOR.MINOR.PATCH (e.g. '1.0.0')"
            )
            raise ValueError(msg)
        return value

    @model_validator(mode="after")
    def _validate_outputs_required(self) -> Self:
        """Ensure all output declarations are required.

        Returns:
            The unmodified ``self``.

        Raises:
            ValueError: If any output declaration has ``required=False``
                (optional outputs are not supported).
        """
        for decl in self.outputs:
            if not decl.required:
                msg = (
                    f"Output declaration {decl.name!r} must be"
                    f" required (optional outputs are not supported)"
                )
                raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_unique_io_names(self) -> Self:
        """Reject duplicate input or output names within the same scope.

        Returns:
            The unmodified ``self``.

        Raises:
            ValueError: If any input or output name appears more than
                once in its respective collection.
        """
        input_names = tuple(decl.name for decl in self.inputs)
        if len(input_names) != len(set(input_names)):
            dupes = sorted(v for v, c in Counter(input_names).items() if c > 1)
            msg = f"Duplicate input declaration names: {dupes}"
            raise ValueError(msg)

        output_names = tuple(decl.name for decl in self.outputs)
        if len(output_names) != len(set(output_names)):
            dupes = sorted(v for v, c in Counter(output_names).items() if c > 1)
            msg = f"Duplicate output declaration names: {dupes}"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_unique_ids(self) -> Self:
        """Reject duplicate node or edge IDs.

        Returns:
            The unmodified ``self``.

        Raises:
            ValueError: If any node id or edge id is not unique.
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
        """Ensure all edges reference existing nodes and no self-loops.

        Returns:
            The unmodified ``self``.

        Raises:
            ValueError: If an edge self-references the same node, or if
                either endpoint id is not in the node set.
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
            The unmodified ``self``.

        Raises:
            ValueError: If the node set has any count other than one
                START node or one END node.
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
