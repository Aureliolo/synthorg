"""Ontology subsystem configuration models."""

from enum import StrEnum
from typing import Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.types import NotBlankStr, validate_unique_strings

# Default divergence score above which a drifted entity definition is
# flagged for operator attention.  Kept as a module-level constant so
# the value has a single, grep-discoverable source of truth; the field
# below re-uses it as its default.
_DRIFT_SCORE_DEFAULT: Final[float] = 0.3

# ── Strategy enums ──────────────────────────────────────────────


class InjectionStrategy(StrEnum):
    """How entity definitions are injected into agent context.

    Attributes:
        PROMPT: Core-tier entities injected as system message section.
        TOOL: On-demand retrieval via ``lookup_entity`` tool only.
        HYBRID: Core entities via system prompt, extended via tool.
        MEMORY: Relies on OrgMemory sync; no direct injection.
    """

    PROMPT = "prompt"
    TOOL = "tool"
    HYBRID = "hybrid"
    MEMORY = "memory"


class DriftStrategy(StrEnum):
    """How entity drift is detected.

    Attributes:
        PASSIVE: Check drift on explicit request only.
        ACTIVE: Periodically scan agent outputs for drift.
        LAYERED: Active for CORE entities, passive for USER entities.
        NONE: Drift detection disabled.
    """

    PASSIVE = "passive"
    ACTIVE = "active"
    LAYERED = "layered"
    NONE = "none"


class GuardMode(StrEnum):
    """Delegation guard enforcement level.

    Attributes:
        NONE: No entity validation on delegation.
        STAMP: Attach canonical definitions to delegated tasks.
        VALIDATE: Warn on entity misuse in delegated tasks.
        ENFORCE: Reject delegations with entity misuse.
    """

    NONE = "none"
    STAMP = "stamp"
    VALIDATE = "validate"
    ENFORCE = "enforce"


# ── Sub-configs ─────────────────────────────────────────────────


class OntologyInjectionConfig(BaseModel):
    """Configuration for entity definition injection into agent context.

    Attributes:
        strategy: How definitions are injected.
        core_token_budget: Max tokens for core entity injection.
        tool_name: Name of the on-demand lookup tool.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    strategy: InjectionStrategy = Field(
        default=InjectionStrategy.HYBRID,
        description="Injection strategy",
    )
    core_token_budget: int = Field(
        default=2000,
        gt=0,
        description="Max tokens for core entity injection",
    )
    tool_name: NotBlankStr = Field(
        default="lookup_entity",
        description="On-demand entity lookup tool name",
    )


class DriftDetectionConfig(BaseModel):
    """Configuration for entity drift detection.

    Attributes:
        strategy: Detection strategy.
        check_interval: Seconds between active drift checks.
        threshold: Divergence score above which drift is flagged.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    strategy: DriftStrategy = Field(
        default=DriftStrategy.PASSIVE,
        description="Drift detection strategy",
    )
    check_interval: int = Field(
        default=300,
        gt=0,
        description="Seconds between active drift checks",
    )
    threshold: float = Field(
        default=_DRIFT_SCORE_DEFAULT,
        ge=0.0,
        le=1.0,
        description="Divergence score threshold for flagging drift",
    )


class DelegationGuardConfig(BaseModel):
    """Configuration for delegation entity validation.

    Attributes:
        guard_mode: Enforcement level for entity validation.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    guard_mode: GuardMode = Field(
        default=GuardMode.STAMP,
        description="Delegation guard enforcement level",
    )


class OntologyMemoryConfig(BaseModel):
    """Configuration for ontology-memory integration.

    Attributes:
        wrapper_enabled: Whether to wrap memory ops with entity tagging.
        auto_tag: Automatically tag stored memories with entity names.
        warn_on_drift: Log warnings when memory content drifts from
            canonical definitions.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    wrapper_enabled: bool = Field(
        default=True,
        description="Enable ontology-aware memory wrapper",
    )
    auto_tag: bool = Field(
        default=True,
        description="Automatically tag memories with entity names",
    )
    warn_on_drift: bool = Field(
        default=True,
        description="Warn when memory content drifts from definitions",
    )


class OntologySyncConfig(BaseModel):
    """Configuration for ontology-organizational memory sync.

    Attributes:
        org_memory_enabled: Whether to sync entity definitions with
            organizational memory.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    org_memory_enabled: bool = Field(
        default=True,
        description="Sync entity definitions with org memory",
    )


# ── User-defined entities ───────────────────────────────────────


class EntityEntry(BaseModel):
    """A single user-defined entity from YAML configuration.

    Attributes:
        name: Entity name.
        definition: Free-text entity description.
        fields: Optional field definitions as name-to-description mapping.
        constraints: Optional business rule descriptions.
        disambiguation: Optional disambiguation text.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(description="Entity name")
    definition: str = Field(
        default="",
        description="Free-text entity description",
    )
    fields: dict[NotBlankStr, str] = Field(
        default_factory=dict,
        description="Field name to description mapping",
    )
    constraints: tuple[str, ...] = Field(
        default=(),
        description="Business rule descriptions",
    )
    disambiguation: str = Field(
        default="",
        description="Disambiguation text",
    )


class EntitiesConfig(BaseModel):
    """Collection of user-defined entity entries from YAML.

    Attributes:
        entries: Tuple of user-defined entity entries.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    entries: tuple[EntityEntry, ...] = Field(
        default=(),
        description="User-defined entity entries",
    )

    @model_validator(mode="after")
    def _validate_unique_names(self) -> Self:
        """Ensure entry names are unique."""
        if self.entries:
            names = tuple(e.name for e in self.entries)
            validate_unique_strings(names, "entries")
        return self


# ── Top-level ontology config ───────────────────────────────────


class OntologyConfig(BaseModel):
    """Top-level ontology subsystem configuration.

    Attributes:
        backend: Backend selection (``"sqlite"`` initially).
        injection: Context injection configuration.
        drift_detection: Drift detection configuration.
        delegation_guard: Delegation guard configuration.
        memory: Memory integration configuration.
        sync: Organizational memory sync configuration.
        entities: User-defined entity entries.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    backend: Literal["sqlite"] = Field(
        default="sqlite",
        description="Ontology backend selection",
    )
    injection: OntologyInjectionConfig = Field(
        default_factory=OntologyInjectionConfig,
        description="Context injection configuration",
    )
    drift_detection: DriftDetectionConfig = Field(
        default_factory=DriftDetectionConfig,
        description="Drift detection configuration",
    )
    delegation_guard: DelegationGuardConfig = Field(
        default_factory=DelegationGuardConfig,
        description="Delegation guard configuration",
    )
    memory: OntologyMemoryConfig = Field(
        default_factory=OntologyMemoryConfig,
        description="Memory integration configuration",
    )
    sync: OntologySyncConfig = Field(
        default_factory=OntologySyncConfig,
        description="Org memory sync configuration",
    )
    entities: EntitiesConfig = Field(
        default_factory=EntitiesConfig,
        description="User-defined entity entries",
    )
