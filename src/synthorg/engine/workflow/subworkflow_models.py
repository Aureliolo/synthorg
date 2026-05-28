"""Domain DTOs for the subworkflow registry surface.

These models are returned by ``SubworkflowRepository`` reads (and
re-emitted by ``SubworkflowRegistry`` / ``SubworkflowService``) but
they are not persistence types in their own right -- the durable rows
are full :class:`WorkflowDefinition` instances. Keeping the DTOs here
lets controllers, MCP handlers, and any other engine-layer caller
depend on engine-domain types instead of reaching into the persistence
package.
"""

from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr


class SubworkflowSummary(BaseModel):
    """Summary information for a subworkflow entry in the registry.

    Used by list / search endpoints that do not need the full node
    and edge payload.

    Attributes:
        subworkflow_id: Stable identifier (shared across versions).
        latest_version: Highest semver currently in the registry.
        name: Human-readable name.
        description: Short description.
        input_count: Number of declared inputs on the latest version.
        output_count: Number of declared outputs on the latest version.
        version_count: Total number of versions in the registry.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    subworkflow_id: NotBlankStr = Field(description="Stable identifier")
    latest_version: NotBlankStr = Field(description="Latest semver")
    name: NotBlankStr = Field(description="Name of the latest version")
    description: str = Field(default="", description="Description")
    input_count: int = Field(ge=0, description="Number of inputs")
    output_count: int = Field(ge=0, description="Number of outputs")
    version_count: int = Field(ge=1, description="Total versions")


class ParentReference(BaseModel):
    """A parent workflow definition that references a given subworkflow.

    Attributes:
        parent_id: Workflow definition ID of the parent.
        parent_name: Display name of the parent.
        pinned_version: Semver of the subworkflow the parent has pinned.
        node_id: Node ID within the parent graph holding the reference.
        parent_type: Whether the parent is a top-level workflow
            definition or another subworkflow.
        parent_version: Parent's semver when ``parent_type`` is
            ``"subworkflow"``; ``None`` for top-level workflows.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    parent_id: NotBlankStr = Field(description="Parent workflow ID")
    parent_name: NotBlankStr = Field(description="Parent workflow name")
    pinned_version: NotBlankStr = Field(description="Pinned semver")
    node_id: NotBlankStr = Field(description="Referencing node ID")
    parent_type: Literal["workflow_definition", "subworkflow"] = Field(
        description="Whether the parent is a workflow definition or subworkflow",
    )
    parent_version: NotBlankStr | None = Field(
        default=None,
        description="Parent's semver when parent_type is subworkflow",
    )

    @model_validator(mode="after")
    def _validate_parent_version_consistency(self) -> Self:
        """Reject inconsistent parent_type / parent_version combinations.

        ``parent_version`` carries the parent subworkflow's own semver
        and is meaningful only when the parent is itself a subworkflow.
        Top-level workflow definitions are mutable and have no
        immutable version coordinate, so ``parent_version`` MUST be
        ``None`` for ``parent_type == "workflow_definition"`` and MUST
        be set for ``parent_type == "subworkflow"``. Constructing the
        DTO with one but not the other handed callers an ambiguous
        shape the registry never produces.

        Returns:
            ``self`` unchanged when parent_type / parent_version
            agree.

        Raises:
            ValueError: When parent is a subworkflow but
                ``parent_version`` is missing, or parent is a top-
                level workflow but ``parent_version`` is set.
        """
        is_subworkflow = self.parent_type == "subworkflow"
        has_version = self.parent_version is not None

        if is_subworkflow and not has_version:
            msg = (
                "parent_version is required when parent_type == 'subworkflow' "
                "(parent subworkflows have an immutable semver coordinate)"
            )
            raise ValueError(msg)
        if not is_subworkflow and has_version:
            msg = (
                "parent_version must be None when parent_type == "
                "'workflow_definition' (top-level workflows have no semver)"
            )
            raise ValueError(msg)
        return self


__all__ = ["ParentReference", "SubworkflowSummary"]
