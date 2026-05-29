"""Ontology domain models.

Frozen Pydantic models representing entity definitions, fields,
relationships, and drift analysis within the semantic ontology.

.. note::

   Enums are defined before ``synthorg.core.types`` imports to avoid
   circular import issues: the ``@ontology_entity`` decorator in
   ``core`` modules needs ``EntityTier`` / ``EntitySource`` during
   class definition, which can trigger a partial-initialization
   cycle through ``core.__init__``.
"""

from datetime import timedelta
from enum import StrEnum
from typing import Self

# ── Enums (defined early -- see module docstring) ───────────────


class EntityTier(StrEnum):
    """Protection tier for entity definitions.

    Attributes:
        CORE: Framework-provided, protected from user modification.
        USER: Domain-specific, editable by users.
    """

    CORE = "core"
    USER = "user"


class EntitySource(StrEnum):
    """Origin of an entity definition.

    Attributes:
        AUTO: Derived from ``@ontology_entity`` decorator.
        CONFIG: Loaded from YAML configuration.
        API: Created via REST API.
    """

    AUTO = "auto"
    CONFIG = "config"
    API = "api"


class DriftAction(StrEnum):
    """Recommended action for addressing entity drift.

    Attributes:
        NO_ACTION: Drift is within acceptable bounds.
        NOTIFY: Alert operators about emerging drift.
        RETRAIN: Retrain divergent agents on canonical definitions.
        ESCALATE: Escalate to human review.
    """

    NO_ACTION = "no_action"
    NOTIFY = "notify"
    RETRAIN = "retrain"
    ESCALATE = "escalate"


# ── Deferred imports (after enums are defined) ──────────────────

from pydantic import (  # noqa: E402
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from synthorg.core.types import (  # noqa: E402
    NotBlankStr,
    validate_unique_strings,
)

# ── Value Objects ───────────────────────────────────────────────


class EntityField(BaseModel):
    """A single field within an entity definition.

    Attributes:
        name: Field name (must not be blank).
        type_hint: Type annotation as a string.
        description: Human-readable description of the field.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(description="Field name")
    type_hint: NotBlankStr = Field(description="Type annotation as string")
    description: str = Field(
        default="",
        description="Human-readable field description",
    )


class EntityRelation(BaseModel):
    """A relationship between two entity definitions.

    Attributes:
        target: Name of the related entity.
        relation: Relationship type (e.g. ``assigned_to``, ``owns``).
        description: Human-readable description of the relationship.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    target: NotBlankStr = Field(description="Related entity name")
    relation: NotBlankStr = Field(description="Relationship type")
    description: str = Field(
        default="",
        description="Human-readable relationship description",
    )


# ── EntityDefinition ────────────────────────────────────────────


class EntityDefinition(BaseModel):
    """A semantic entity definition in the ontology.

    Represents a named concept (Task, Agent, Role, etc.) with its
    fields, constraints, disambiguation text, and relationships.
    Immutable once created -- updates produce new versions via the
    versioning service.

    Attributes:
        name: Unique entity name (e.g. ``Task``, ``AgentIdentity``).
        tier: Protection tier (core or user).
        source: How this definition was created.
        definition: Free-text description of the entity.
        fields: Typed field descriptors.
        constraints: Business rule descriptions.
        disambiguation: Text clarifying what this entity is *not*.
        relationships: Relationships to other entities.
        created_by: Identifier of the actor who created this definition.
        created_at: Creation timestamp (must be UTC).
        updated_at: Last update timestamp (must be UTC).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(description="Unique entity name")
    tier: EntityTier = Field(description="Protection tier")
    source: EntitySource = Field(description="Definition origin")
    definition: str = Field(
        default="",
        description="Free-text entity description",
    )
    fields: tuple[EntityField, ...] = Field(
        default=(),
        description="Typed field descriptors",
    )
    constraints: tuple[str, ...] = Field(
        default=(),
        description="Business rule descriptions",
    )
    disambiguation: str = Field(
        default="",
        description="Clarification of what this entity is not",
    )
    relationships: tuple[EntityRelation, ...] = Field(
        default=(),
        description="Relationships to other entities",
    )
    created_by: NotBlankStr = Field(
        description="Actor who created this definition",
    )
    created_at: AwareDatetime = Field(description="Creation timestamp (UTC)")
    updated_at: AwareDatetime = Field(description="Last update timestamp (UTC)")

    @field_validator("created_at", "updated_at")
    @classmethod
    def _validate_utc(cls, v: AwareDatetime) -> AwareDatetime:
        """Reject non-UTC timestamps.

        Returns:
            The validated timestamp, unchanged.

        Raises:
            ValueError: When the timestamp's UTC offset is non-zero.
        """
        if v.utcoffset() != timedelta(0):
            msg = "must be UTC"
            raise ValueError(msg)
        return v

    @model_validator(mode="after")
    def _validate_unique_field_names(self) -> Self:
        """Ensure field names are unique within the definition.

        Returns:
            The validated model instance (``self``), unchanged.
        """
        if self.fields:
            names = tuple(f.name for f in self.fields)
            validate_unique_strings(names, "fields")
        return self

    @model_validator(mode="after")
    def _validate_unique_relationships(self) -> Self:
        """Ensure (target, relation) pairs are unique.

        Returns:
            The validated model instance (``self``), unchanged.

        Raises:
            ValueError: When two relationships share the same
                ``(target, relation)`` pair.
        """
        if self.relationships:
            pairs: list[tuple[str, str]] = []
            for r in self.relationships:
                pair = (r.target, r.relation)
                if pair in pairs:
                    msg = f"Duplicate relationship ({r.target!r}, {r.relation!r})"
                    raise ValueError(msg)
                pairs.append(pair)
        return self


# ── Drift Models ────────────────────────────────────────────────


class AgentDrift(BaseModel):
    """Per-agent drift detail within a drift report.

    Attributes:
        agent_id: Identifier of the divergent agent.
        divergence_score: How far the agent diverges (0.0--1.0).
        details: Human-readable description of the divergence.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    agent_id: NotBlankStr = Field(description="Divergent agent identifier")
    divergence_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Divergence magnitude (0.0 = aligned, 1.0 = fully divergent)",
    )
    details: str = Field(
        default="",
        description="Human-readable divergence description",
    )


class DriftReport(BaseModel):
    """Drift analysis report for an entity definition.

    Attributes:
        entity_name: Name of the entity being analyzed.
        divergence_score: Aggregate divergence across agents (0.0--1.0).
        divergent_agents: Per-agent drift details.
        canonical_version: Version number of the canonical definition.
        recommendation: Recommended corrective action.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    entity_name: NotBlankStr = Field(description="Entity being analyzed")
    divergence_score: float = Field(
        ge=0.0,
        le=1.0,
        description="Aggregate divergence (0.0 = aligned, 1.0 = fully divergent)",
    )
    divergent_agents: tuple[AgentDrift, ...] = Field(
        default=(),
        description="Per-agent drift details",
    )
    canonical_version: int = Field(
        ge=1,
        description="Version of the canonical definition",
    )
    recommendation: DriftAction = Field(
        description="Recommended corrective action",
    )
