"""Agent identity and configuration models."""

from datetime import date  # noqa: TC003 — required at runtime by Pydantic
from typing import Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ai_company.core.enums import (
    AgentStatus,
    CreativityLevel,
    MemoryType,
    RiskTolerance,
    SeniorityLevel,
)
from ai_company.core.role import Authority
from ai_company.core.types import NotBlankStr  # noqa: TC001


class PersonalityConfig(BaseModel):
    """Personality traits and communication style for an agent.

    Attributes:
        traits: Personality trait keywords.
        communication_style: Free-text style description.
        risk_tolerance: Risk tolerance level.
        creativity: Creativity level.
        description: Extended personality description.
    """

    model_config = ConfigDict(frozen=True)

    traits: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Personality traits",
    )
    communication_style: NotBlankStr = Field(
        default="neutral",
        description="Communication style description",
    )
    risk_tolerance: RiskTolerance = Field(
        default=RiskTolerance.MEDIUM,
        description="Risk tolerance level",
    )
    creativity: CreativityLevel = Field(
        default=CreativityLevel.MEDIUM,
        description="Creativity level",
    )
    description: str = Field(
        default="",
        description="Extended personality description",
    )


class SkillSet(BaseModel):
    """Primary and secondary skills for an agent.

    Attributes:
        primary: Core competency skill names.
        secondary: Supporting skill names.
    """

    model_config = ConfigDict(frozen=True)

    primary: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Primary skills",
    )
    secondary: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Secondary skills",
    )


class ModelConfig(BaseModel):
    """LLM model configuration for an agent.

    Attributes:
        provider: LLM provider name (e.g. ``"anthropic"``).
        model_id: Model identifier (e.g. ``"claude-sonnet-4-6"``).
        temperature: Sampling temperature (0.0 to 2.0).
        max_tokens: Maximum output tokens.
        fallback_model: Optional fallback model identifier.
    """

    model_config = ConfigDict(frozen=True)

    provider: NotBlankStr = Field(description="LLM provider name")
    model_id: NotBlankStr = Field(description="Model identifier")
    temperature: float = Field(
        default=0.7,
        ge=0.0,
        le=2.0,
        description="Sampling temperature",
    )
    max_tokens: int = Field(
        default=4096,
        gt=0,
        description="Maximum output tokens",
    )
    fallback_model: NotBlankStr | None = Field(
        default=None,
        description="Fallback model identifier",
    )


class MemoryConfig(BaseModel):
    """Memory configuration for an agent.

    Attributes:
        type: Memory persistence type.
        retention_days: Days to retain memories (``None`` means forever).
    """

    model_config = ConfigDict(frozen=True)

    type: MemoryType = Field(
        default=MemoryType.SESSION,
        description="Memory persistence type",
    )
    retention_days: int | None = Field(
        default=None,
        ge=1,
        description="Days to retain memories (None = forever)",
    )

    @model_validator(mode="after")
    def _validate_retention_consistency(self) -> Self:
        """Ensure retention_days is None when memory type is MemoryType.NONE."""
        if self.type is MemoryType.NONE and self.retention_days is not None:
            msg = "retention_days must be None when memory type is 'none'"
            raise ValueError(msg)
        return self


class ToolPermissions(BaseModel):
    """Tool access permissions for an agent.

    Attributes:
        allowed: Explicitly allowed tool names.
        denied: Explicitly denied tool names.
    """

    model_config = ConfigDict(frozen=True)

    allowed: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Explicitly allowed tools",
    )
    denied: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Explicitly denied tools",
    )

    @model_validator(mode="after")
    def _validate_no_overlap(self) -> Self:
        """Ensure no tool appears in both allowed and denied lists.

        Comparison is case-insensitive.
        """
        allowed_normalized = {t.strip().casefold() for t in self.allowed}
        denied_normalized = {t.strip().casefold() for t in self.denied}
        overlap = allowed_normalized & denied_normalized
        if overlap:
            msg = f"Tools appear in both allowed and denied lists: {sorted(overlap)}"
            raise ValueError(msg)
        return self


class AgentIdentity(BaseModel):
    """Complete agent identity card.

    Every agent in the company is represented by an ``AgentIdentity``
    containing its role, personality, model backend, memory settings,
    tool permissions, and authority configuration.

    Attributes:
        id: Unique agent identifier.
        name: Agent display name.
        role: Role name (string reference to :class:`~ai_company.core.role.Role`).
        department: Department name (string reference).
        level: Seniority level.
        personality: Personality configuration.
        skills: Primary and secondary skill set.
        model: LLM model configuration.
        memory: Memory configuration.
        tools: Tool permissions.
        authority: Authority configuration for this agent.
        hiring_date: Date the agent was hired.
        status: Current lifecycle status.
    """

    model_config = ConfigDict(frozen=True)

    id: UUID = Field(default_factory=uuid4, description="Unique agent identifier")
    name: NotBlankStr = Field(description="Agent display name")
    role: NotBlankStr = Field(description="Role name")
    department: NotBlankStr = Field(description="Department name")
    level: SeniorityLevel = Field(
        default=SeniorityLevel.MID,
        description="Seniority level",
    )
    personality: PersonalityConfig = Field(
        default_factory=PersonalityConfig,
        description="Personality configuration",
    )
    skills: SkillSet = Field(
        default_factory=SkillSet,
        description="Skill set",
    )
    model: ModelConfig = Field(description="LLM model configuration")
    memory: MemoryConfig = Field(
        default_factory=MemoryConfig,
        description="Memory configuration",
    )
    tools: ToolPermissions = Field(
        default_factory=ToolPermissions,
        description="Tool permissions",
    )
    authority: Authority = Field(
        default_factory=Authority,
        description="Authority scope",
    )
    hiring_date: date = Field(description="Date the agent was hired")
    status: AgentStatus = Field(
        default=AgentStatus.ACTIVE,
        description="Current lifecycle status",
    )
