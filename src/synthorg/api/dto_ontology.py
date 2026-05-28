"""Request and response DTOs for the ontology REST API."""

from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr
from synthorg.ontology.models import (
    DriftAction,
    EntitySource,
    EntityTier,
)

# ── Request DTOs ───────────────────────────────────────────────


class EntityFieldInput(BaseModel):
    """Field definition for entity create/update."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(description="Field name")
    type_hint: NotBlankStr = Field(description="Type annotation")
    description: str = Field(default="", description="Field description")


class EntityRelationInput(BaseModel):
    """Relationship definition for entity create/update."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    target: NotBlankStr = Field(description="Related entity name")
    relation: NotBlankStr = Field(description="Relationship type")
    description: str = Field(default="", description="Relationship description")


class CreateEntityRequest(BaseModel):
    """Payload for creating a new entity definition (USER tier only)."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(description="Entity name", max_length=256)
    definition: str = Field(
        default="",
        description="Free-text entity description",
        max_length=4096,
    )
    fields: tuple[EntityFieldInput, ...] = Field(
        default=(),
        description="Field definitions",
    )
    constraints: tuple[str, ...] = Field(
        default=(),
        description="Business rule descriptions",
    )
    disambiguation: str = Field(
        default="",
        description="What this entity is NOT",
        max_length=2048,
    )
    relationships: tuple[EntityRelationInput, ...] = Field(
        default=(),
        description="Entity relationships",
    )


class UpdateEntityRequest(BaseModel):
    """Payload for updating an entity definition."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    definition: str | None = Field(
        default=None,
        description="Updated description",
        max_length=4096,
    )
    fields: tuple[EntityFieldInput, ...] | None = Field(
        default=None,
        description="Updated fields",
    )
    constraints: tuple[str, ...] | None = Field(
        default=None,
        description="Updated constraints",
    )
    disambiguation: str | None = Field(
        default=None,
        description="Updated disambiguation",
        max_length=2048,
    )
    relationships: tuple[EntityRelationInput, ...] | None = Field(
        default=None,
        description="Updated relationships",
    )

    @model_validator(mode="after")
    def _validate_not_empty(self) -> Self:
        """Ensure at least one field is being updated.

        Returns:
            ``Self`` instance.

        Raises:
            ValueError: Raised on the corresponding failure path.
        """
        if not any(
            [
                self.definition is not None,
                self.fields is not None,
                self.constraints is not None,
                self.disambiguation is not None,
                self.relationships is not None,
            ]
        ):
            msg = "At least one field must be specified for update"
            raise ValueError(msg)
        return self


# ── Response DTOs ──────────────────────────────────────────────


class EntityFieldResponse(BaseModel):
    """Field in an entity definition response."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr
    type_hint: NotBlankStr
    description: str = ""


class EntityRelationResponse(BaseModel):
    """Relationship in an entity definition response."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    target: NotBlankStr
    relation: NotBlankStr
    description: str = ""


class EntityResponse(BaseModel):
    """Entity definition response."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr
    tier: EntityTier
    source: EntitySource
    definition: str = ""
    fields: tuple[EntityFieldResponse, ...] = ()
    constraints: tuple[str, ...] = ()
    disambiguation: str = ""
    relationships: tuple[EntityRelationResponse, ...] = ()
    created_by: NotBlankStr
    created_at: AwareDatetime
    updated_at: AwareDatetime


class EntityVersionResponse(BaseModel):
    """Version snapshot response."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    entity_id: NotBlankStr
    version: int = Field(ge=1)
    content_hash: NotBlankStr
    snapshot: EntityResponse
    saved_by: NotBlankStr
    saved_at: AwareDatetime


class DriftAgentResponse(BaseModel):
    """Per-agent drift detail in a drift report response."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_id: NotBlankStr
    divergence_score: float = Field(ge=0.0, le=1.0)
    details: str = ""


class DriftReportResponse(BaseModel):
    """Drift report response."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    entity_name: NotBlankStr
    divergence_score: float = Field(ge=0.0, le=1.0)
    divergent_agents: tuple[DriftAgentResponse, ...] = ()
    canonical_version: int = Field(ge=1)
    recommendation: DriftAction


class DriftSummary(BaseModel):
    """Aggregate drift metrics for entity list enrichment."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    avg_drift_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Average divergence across all entities",
    )
    entities_with_drift: int = Field(
        ge=0,
        description="Count of entities above drift threshold",
    )


class EntityListMeta(BaseModel):
    """Enrichment metadata for entity list responses."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    total_count: int = Field(ge=0)
    core_count: int = Field(ge=0)
    user_count: int = Field(ge=0)
    drift_summary: DriftSummary | None = Field(
        default=None,
        description="Drift aggregate (None when drift store unavailable)",
    )
