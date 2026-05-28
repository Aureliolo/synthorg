"""Artifact domain models for task outputs and expected deliverables."""

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.core.enums import (
    ArtifactType,
)
from synthorg.core.types import NotBlankStr
from synthorg.ontology.decorator import ontology_entity


class ExpectedArtifact(BaseModel):
    """An artifact expected to be produced by a task.

    Used within task definitions to declare what outputs are expected.

    Attributes:
        type: The type of artifact expected.
        path: File or directory path where the artifact should be produced.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    type: ArtifactType = Field(description="Type of artifact expected")
    path: NotBlankStr = Field(
        description="File or directory path for the artifact",
    )


@ontology_entity
class Artifact(BaseModel):
    """A concrete artifact produced by an agent during task execution.

    Artifacts track the actual work output, linking it back to the
    originating task and the agent who produced it.

    Attributes:
        id: Unique artifact identifier (e.g. ``"artifact-abc123"``).
        type: The type of artifact.
        path: File or directory path of the artifact.
        task_id: ID of the task that produced this artifact.
        created_by: Agent ID of the creator.
        description: Human-readable description of the artifact.
        content_type: MIME content type (empty when no content stored).
        size_bytes: Content size in bytes (zero when no content stored).
        project_id: ID of the project this artifact belongs to.
        created_at: Timestamp when the artifact was created.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(description="Unique artifact identifier")
    type: ArtifactType = Field(description="Artifact type")
    path: NotBlankStr = Field(
        description="File or directory path of the artifact",
    )
    task_id: NotBlankStr = Field(
        description="ID of the task that produced this artifact",
    )
    created_by: NotBlankStr = Field(
        description="Agent ID of the creator",
    )
    description: str = Field(
        default="",
        description="Human-readable description of the artifact",
    )
    project_id: NotBlankStr | None = Field(
        default=None,
        description="ID of the project this artifact belongs to",
    )
    content_type: str = Field(
        default="",
        description="MIME content type (empty when no content stored)",
    )
    size_bytes: int = Field(
        default=0,
        ge=0,
        description="Content size in bytes (zero when no content stored)",
    )
    created_at: AwareDatetime | None = Field(
        default=None,
        description="UTC timestamp when the artifact was created",
    )
