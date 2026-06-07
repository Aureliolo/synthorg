"""Workflow definition request/response DTOs.

Holds the workflow-definition request/response models consumed by the
workflow HTTP controllers. ``RollbackWorkflowRequest`` is NOT here: it
lives in ``synthorg.versioning.models`` so the rollback service
validates the same shape without importing the API layer.
"""

import json
import re
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.enums import WorkflowType, WorkflowValueType

_SEMVER_RE = re.compile(
    r"^(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)$",
)

_TYPE_CHECKS: dict[WorkflowValueType, type | tuple[type, ...]] = {
    WorkflowValueType.STRING: str,
    WorkflowValueType.INTEGER: int,
    WorkflowValueType.FLOAT: (int, float),
    WorkflowValueType.BOOLEAN: bool,
    WorkflowValueType.DATETIME: str,
}


def _validate_default_type(
    name: str,
    declared_type: WorkflowValueType,
    default: object,
) -> None:
    """Reject defaults that are not compatible with the declared type.

    Raises:
        TypeError: Raised on the corresponding failure path.
        ValueError: Raised on the corresponding failure path.
    """
    try:
        json.dumps(default, allow_nan=False)
    except TypeError as exc:
        msg = (
            f"Declaration {name!r}: default value {default!r} is not JSON-serializable"
        )
        raise ValueError(msg) from exc
    except ValueError as exc:
        msg = f"Declaration {name!r}: default value {default!r} contains NaN/Inf"
        raise ValueError(msg) from exc
    expected = _TYPE_CHECKS.get(declared_type)
    if expected is None:
        # JSON, TASK_REF, AGENT_REF -- accept any JSON-serializable value.
        return
    # bool subclasses int -- reject booleans for integer/float.
    if isinstance(default, bool) and declared_type in (
        WorkflowValueType.INTEGER,
        WorkflowValueType.FLOAT,
    ):
        msg = (
            f"Declaration {name!r}: default value {default!r} is not "
            f"compatible with type {declared_type.value!r}"
        )
        raise TypeError(msg)
    if not isinstance(default, expected):
        msg = (
            f"Declaration {name!r}: default value {default!r} is not "
            f"compatible with type {declared_type.value!r}"
        )
        raise TypeError(msg)


class WorkflowIODeclarationRequest(BaseModel):
    """Typed input/output declaration for workflow creation/update.

    Field names mirror ``WorkflowIODeclaration`` so that
    ``model_validate`` pass-through works without renaming.

    Attributes:
        name: Identifier for this input or output.
        type: The declared data type.
        required: Whether this declaration is mandatory.
        default: Default when not required (must be None when required).
        description: Human-readable description.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(max_length=128, description="Declaration name")
    type: WorkflowValueType = Field(description="Declared data type")
    required: bool = Field(default=True, description="Whether mandatory")
    default: object = Field(default=None, description="Default value")
    description: str = Field(
        default="",
        max_length=1024,
        description="Human-readable description",
    )

    @model_validator(mode="after")
    def _validate_default_with_required(self) -> Self:
        """Reject defaults on required declarations at the DTO boundary.

        Also validates that non-None defaults are type-compatible with
        the declared ``type`` and JSON-serializable.

        Returns:
            ``Self`` instance.

        Raises:
            ValueError: Raised on the corresponding failure path.
        """
        if self.required and self.default is not None:
            msg = (
                f"Declaration {self.name!r}: required declarations "
                f"must not carry a default value"
            )
            raise ValueError(msg)
        if self.default is not None:
            _validate_default_type(self.name, self.type, self.default)
        return self


class CreateWorkflowDefinitionRequest(BaseModel):
    """Payload for creating a new workflow definition.

    Attributes:
        name: Workflow name.
        description: Optional description.
        workflow_type: Target execution topology.
        nodes: Nodes in the workflow graph (serialized as dicts).
        edges: Edges connecting nodes (serialized as dicts).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(max_length=256, description="Workflow name")
    description: str = Field(
        default="",
        max_length=4096,
        description="Description",
    )
    workflow_type: WorkflowType = Field(
        description="Target execution topology",
    )
    version: str = Field(
        default="1.0.0",
        max_length=64,
        description="Semver version string",
    )
    inputs: tuple[WorkflowIODeclarationRequest, ...] = Field(
        default=(),
        max_length=100,
        description="Typed input declarations",
    )
    outputs: tuple[WorkflowIODeclarationRequest, ...] = Field(
        default=(),
        max_length=100,
        description="Typed output declarations",
    )
    is_subworkflow: bool = Field(
        default=False,
        description="Whether this definition is a reusable subworkflow",
    )
    nodes: tuple[dict[str, object], ...] = Field(
        max_length=500,
        description="Workflow nodes",
    )
    edges: tuple[dict[str, object], ...] = Field(
        max_length=1000,
        description="Workflow edges",
    )

    @field_validator("version")
    @classmethod
    def _validate_semver(cls, v: str) -> str:
        """Validate semver.

        Returns:
            Resulting string.

        Raises:
            ValueError: Raised on the corresponding failure path.
        """
        if not _SEMVER_RE.match(v):
            msg = f"Invalid semver: {v!r} (expected MAJOR.MINOR.PATCH)"
            raise ValueError(msg)
        return v


class UpdateWorkflowDefinitionRequest(BaseModel):
    """Payload for updating an existing workflow definition.

    All fields are optional -- only provided fields are updated.

    Attributes:
        name: New name.
        description: New description.
        workflow_type: New workflow type.
        version: New semver version string.
        inputs: New typed input contract.
        outputs: New typed output contract.
        is_subworkflow: New publishing flag.
        nodes: New nodes.
        edges: New edges.
        expected_revision: Optimistic concurrency guard.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr | None = Field(default=None, max_length=256)
    description: str | None = Field(default=None, max_length=4096)
    workflow_type: WorkflowType | None = None
    version: NotBlankStr | None = Field(
        default=None,
        max_length=64,
        description="Semver string override",
    )
    inputs: tuple[WorkflowIODeclarationRequest, ...] | None = Field(
        default=None,
        max_length=100,
    )
    outputs: tuple[WorkflowIODeclarationRequest, ...] | None = Field(
        default=None,
        max_length=100,
    )

    @field_validator("version")
    @classmethod
    def _validate_semver(cls, v: str | None) -> str | None:
        """Validate semver.

        Returns:
            The ``str`` when present, otherwise ``None``.

        Raises:
            ValueError: Raised on the corresponding failure path.
        """
        if v is not None and not _SEMVER_RE.match(v):
            msg = f"Invalid semver: {v!r} (expected MAJOR.MINOR.PATCH)"
            raise ValueError(msg)
        return v

    is_subworkflow: bool | None = None
    nodes: tuple[dict[str, object], ...] | None = Field(
        default=None,
        max_length=500,
    )
    edges: tuple[dict[str, object], ...] | None = Field(
        default=None,
        max_length=1000,
    )
    expected_revision: int | None = Field(
        default=None,
        ge=1,
        description="Optimistic concurrency guard (revision counter)",
    )


class ActivateWorkflowRequest(BaseModel):
    """Request body for activating a workflow definition.

    Attributes:
        project: Project ID for all created tasks.
        context: Runtime context for condition expression evaluation.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    project: NotBlankStr = Field(
        description="Project ID for created tasks",
    )
    context: dict[str, str | int | float | bool | None] = Field(
        default_factory=dict,
        max_length=64,
        description="Runtime context for condition evaluation",
    )


class BlueprintInfoResponse(BaseModel):
    """Response body for a single workflow blueprint entry.

    Attributes:
        name: Blueprint identifier.
        display_name: Human-readable name.
        description: Short description.
        source: Origin of the blueprint.
        tags: Categorization tags.
        workflow_type: Target execution topology.
        node_count: Number of nodes in the graph.
        edge_count: Number of edges in the graph.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(description="Blueprint identifier")
    display_name: NotBlankStr = Field(description="Human-readable name")
    description: str = Field(default="", description="Short description")
    source: Literal["builtin", "user"] = Field(
        description="Origin: builtin or user",
    )
    tags: tuple[NotBlankStr, ...] = Field(default=(), description="Tags")
    workflow_type: WorkflowType = Field(
        description="Target workflow type",
    )
    node_count: int = Field(ge=0, description="Number of nodes")
    edge_count: int = Field(ge=0, description="Number of edges")


class CreateFromBlueprintRequest(BaseModel):
    """Request body for creating a workflow from a blueprint.

    Attributes:
        blueprint_name: Name of the blueprint to instantiate.
        name: Optional name override (defaults to blueprint display_name).
        description: Optional description override.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    blueprint_name: NotBlankStr = Field(
        max_length=128,
        description="Blueprint to instantiate",
    )
    name: NotBlankStr | None = Field(
        default=None,
        max_length=256,
        description="Workflow name override",
    )
    description: str | None = Field(
        default=None,
        max_length=4096,
        description="Description override",
    )
