"""Request/response models for the first-run setup controller."""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from synthorg.core.enums import AutonomyLevel, SeniorityLevel, SkillPattern
from synthorg.core.normalization import normalize_ascii_lowercase
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.templates.model_requirements import ModelTier  # noqa: TC001


def _normalize_and_validate_preset(
    raw: object,
    fallback: str = "",
) -> str:
    """Normalize *raw* to a valid personality preset key.

    Args:
        raw: Raw preset value from the request payload.
        fallback: Default key when *raw* is falsy.

    Returns:
        Normalized lowercase preset key.

    Raises:
        ValueError: If the resolved key is not in ``PERSONALITY_PRESETS``.
    """
    from synthorg.templates.presets import (  # noqa: PLC0415
        PERSONALITY_PRESETS,
    )

    if not raw or not str(raw).strip():
        if not fallback:
            msg = "personality_preset is required"
            raise ValueError(msg)
        key = fallback
    else:
        key = normalize_ascii_lowercase(str(raw))

    if key not in PERSONALITY_PRESETS:
        available = sorted(PERSONALITY_PRESETS)
        msg = f"Unknown personality preset {raw!r}. Available: {available}"
        raise ValueError(msg)
    return key


class SetupStatusResponse(BaseModel):
    """First-run setup status.

    Attributes:
        needs_admin: True if no user with the CEO role exists yet.
        needs_setup: True if setup has not been completed.
        has_providers: True if at least one provider is configured.
        has_name_locales: True if name locale preferences have been configured.
        has_company: True if a company name has been set.
        has_agents: True if at least one agent has been created.
        min_password_length: Backend-configured minimum password length.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    needs_admin: bool
    needs_setup: bool
    has_providers: bool
    has_name_locales: bool
    has_company: bool
    has_agents: bool
    min_password_length: int = Field(ge=8)


class TemplateVariableResponse(BaseModel):
    """A template variable exposed to the frontend.

    Attributes:
        name: Variable name used in Jinja2 placeholders.
        description: Human-readable description.
        var_type: Expected value type (one of "str", "int", "float", "bool").
        default: Default value (None means no default).
        required: Whether the user must supply a value.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr
    description: str = ""
    var_type: Literal["str", "int", "float", "bool"] = "str"
    default: str | int | float | bool | None = None
    required: bool = False


class TemplateInfoResponse(BaseModel):
    """Summary of an available company template.

    Attributes:
        name: Template identifier.
        display_name: Human-readable name.
        description: Short description.
        source: Where the template was found (builtin or user).
        tags: Free-form categorization tags for template filtering and discovery.
        skill_patterns: Skill design pattern identifiers describing how the
            template's agents interact (e.g. ``"tool_wrapper"``, ``"pipeline"``).
        variables: Template variables the user can configure.
        agent_count: Number of agents defined in the template.
        department_count: Number of departments defined in the template.
        autonomy_level: Autonomy level (e.g. ``"full"``, ``"semi"``).
        workflow: Workflow type (e.g. ``"agile_kanban"``, ``"kanban"``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    name: NotBlankStr
    display_name: NotBlankStr
    description: str
    source: Literal["builtin", "user"]
    tags: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Categorization tags for filtering and discovery",
    )
    skill_patterns: tuple[SkillPattern, ...] = Field(
        default=(),
        description="Skill design pattern identifiers",
    )
    variables: tuple[TemplateVariableResponse, ...] = Field(
        default=(),
        description="User-configurable template variables",
    )
    agent_count: int = Field(
        default=0,
        ge=0,
        description="Number of agents defined in the template",
    )
    department_count: int = Field(
        default=0,
        ge=0,
        description="Number of departments defined in the template",
    )
    autonomy_level: AutonomyLevel = Field(
        default=AutonomyLevel.SEMI,
        description="Autonomy level (full, semi, supervised, locked)",
    )
    workflow: NotBlankStr = Field(
        default="agile_kanban",
        description="Workflow type (agile_kanban, kanban, etc.)",
    )


class SetupCompanyRequest(BaseModel):
    """Company creation payload for first-run setup.

    Attributes:
        company_name: Company display name.
        description: Optional company description.
        template_name: Optional template to apply (None = blank company).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    company_name: NotBlankStr = Field(
        max_length=200,
        examples=["Hooli", "Pied Piper"],
    )
    description: str | None = Field(
        default=None,
        max_length=1000,
        examples=["Boutique consultancy specializing in agentic ops"],
    )
    template_name: NotBlankStr | None = Field(
        default=None,
        max_length=100,
        examples=["consulting-firm", "blank"],
    )


class SetupAgentSummary(BaseModel):
    """Summary of an agent for the Review Org step.

    Attributes:
        name: Agent display name.
        role: Agent role.
        department: Assigned department.
        level: Seniority level (``None`` if not specified).
        model_provider: LLM provider name (``None`` if unassigned).
        model_id: Model identifier (``None`` if unassigned).
        tier: Original tier requirement from the template.
        personality_preset: Personality preset name, if any.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr
    role: NotBlankStr
    department: NotBlankStr
    level: SeniorityLevel | None = None
    model_provider: NotBlankStr | None = None
    model_id: NotBlankStr | None = None
    tier: ModelTier = "medium"
    personality_preset: NotBlankStr | None = None


class SetupCompanyResponse(BaseModel):
    """Company creation result.

    Attributes:
        company_name: The company name that was set.
        description: The company description that was set, if any.
        template_applied: Name of the template that was applied, if any.
        department_count: Number of departments created.
        agent_count: Number of agents auto-created from template
            (computed from ``agents``).
        agents: Agent summaries for the Review Org step.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    company_name: NotBlankStr
    description: str | None
    template_applied: NotBlankStr | None
    department_count: int = Field(ge=0)
    agents: tuple[SetupAgentSummary, ...] = ()

    @computed_field  # type: ignore[prop-decorator]
    @property
    def agent_count(self) -> int:
        """Number of agents auto-created from template."""
        return len(self.agents)


class SetupAgentRequest(BaseModel):
    """Agent creation payload for first-run setup.

    Attributes:
        name: Agent display name.
        role: Agent role name.
        level: Seniority level.
        personality_preset: Personality preset name.
        model_provider: Provider name for the agent's model.
        model_id: Model identifier from that provider.
        department: Department to assign the agent to.
        budget_limit_monthly: Optional monthly budget limit in base currency.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    name: NotBlankStr = Field(max_length=200, examples=["Alice Lin", "Bob Chen"])
    role: NotBlankStr = Field(max_length=100, examples=["CEO", "Engineer", "Designer"])
    level: SeniorityLevel = Field(default=SeniorityLevel.MID)
    personality_preset: NotBlankStr = Field(
        default="pragmatic_builder",
        max_length=100,
        examples=["pragmatic_builder", "visionary_leader"],
    )
    model_provider: NotBlankStr = Field(
        max_length=100,
        examples=["example-provider"],
    )
    model_id: NotBlankStr = Field(
        max_length=200,
        examples=["example-medium-001", "example-large-001"],
    )
    department: NotBlankStr = Field(
        default="engineering",
        max_length=100,
        examples=["engineering", "design", "operations"],
    )
    budget_limit_monthly: float | None = Field(
        default=None,
        ge=0.0,
        le=1_000_000.0,
        examples=[100.0, 500.0],
    )

    @model_validator(mode="before")
    @classmethod
    def _validate_preset_exists(cls, values: Any) -> Any:
        """Normalize and validate the personality preset before construction."""
        if not isinstance(values, dict):
            return values
        raw = values.get("personality_preset", "pragmatic_builder")
        normalized = _normalize_and_validate_preset(
            raw,
            fallback="pragmatic_builder",
        )
        return {**values, "personality_preset": normalized}


class SetupAgentResponse(BaseModel):
    """Agent creation result.

    Attributes:
        name: Agent display name.
        role: Agent role.
        department: Assigned department.
        model_provider: LLM provider name.
        model_id: Model identifier.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr
    role: NotBlankStr
    department: NotBlankStr
    model_provider: NotBlankStr
    model_id: NotBlankStr


class UpdateAgentModelRequest(BaseModel):
    """Request to update an agent's model assignment during setup.

    Attributes:
        model_provider: Provider name.
        model_id: Model identifier from that provider.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    model_provider: NotBlankStr = Field(max_length=100)
    model_id: NotBlankStr = Field(max_length=200)


class UpdateAgentNameRequest(BaseModel):
    """Request to update an agent's display name during setup.

    Attributes:
        name: New agent display name.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    name: NotBlankStr = Field(max_length=200)


class UpdateAgentPersonalityRequest(BaseModel):
    """Request to update an agent's personality preset during setup.

    Attributes:
        personality_preset: Personality preset name (must exist in
            ``PERSONALITY_PRESETS``).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    personality_preset: NotBlankStr = Field(max_length=100)

    @model_validator(mode="before")
    @classmethod
    def _validate_preset_exists(cls, values: Any) -> Any:
        """Normalize and validate the personality preset."""
        if not isinstance(values, dict):
            return values
        raw = values.get("personality_preset")
        normalized = _normalize_and_validate_preset(raw)
        return {**values, "personality_preset": normalized}


class PersonalityPresetInfoResponse(BaseModel):
    """Summary of a personality preset for the setup wizard.

    Attributes:
        name: Preset identifier key.
        description: Human-readable description.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    name: NotBlankStr
    description: str = ""


class SetupNameLocalesRequest(BaseModel):
    """Name locale selection payload.

    Attributes:
        locales: List of Faker locale codes (1--100 entries), or
            ``["__all__"]`` for all Latin-script locales.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    locales: list[NotBlankStr] = Field(min_length=1, max_length=100)


class SetupNameLocalesResponse(BaseModel):
    """Current name locale configuration.

    Attributes:
        locales: Stored locale codes (``["__all__"]`` if worldwide).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    locales: list[NotBlankStr]


class AvailableLocalesResponse(BaseModel):
    """Available locales grouped by region.

    Attributes:
        regions: Mapping of region display name to locale codes.
        display_names: Mapping of locale code to human-readable name.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    regions: dict[str, list[str]]
    display_names: dict[str, str]


class SetupCompleteResponse(BaseModel):
    """Setup completion result.

    Attributes:
        setup_complete: Always True on success.
        embedder_selected: True when ``auto_select_embedder`` succeeded
            and persisted an ``memory.embedder_model`` choice. False
            when auto-selection failed (no LMEB-ranked model available,
            persistence error). The wizard's post-completion guidance
            uses this flag to surface a warning instead of silently
            shipping the operator to a half-configured memory backend.
        embedder_failure_reason: Short human-readable reason when
            auto-selection failed. ``None`` on success.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    setup_complete: Literal[True]
    embedder_selected: bool = True
    embedder_failure_reason: str | None = None

    @model_validator(mode="after")
    def _validate_embedder_state_consistency(self) -> SetupCompleteResponse:
        if self.embedder_selected and self.embedder_failure_reason is not None:
            msg = "embedder_failure_reason must be None when embedder_selected=True"
            raise ValueError(msg)
        if not self.embedder_selected and not self.embedder_failure_reason:
            msg = (
                "embedder_failure_reason must be a non-empty string when"
                " embedder_selected=False"
            )
            raise ValueError(msg)
        return self
