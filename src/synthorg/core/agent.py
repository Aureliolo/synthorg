"""Agent identity and configuration models."""

from datetime import date  # noqa: TC003 — required at runtime by Pydantic
from typing import Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.enums import (
    AgentStatus,
    AutonomyLevel,
    CollaborationPreference,
    CommunicationVerbosity,
    ConflictApproach,
    CreativityLevel,
    DecisionMakingStyle,
    MemoryLevel,
    RiskTolerance,
    SeniorityLevel,
    ToolAccessLevel,
)
from synthorg.core.role import Authority
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.config import CONFIG_VALIDATION_FAILED

logger = get_logger(__name__)


class PersonalityConfig(BaseModel):
    """Personality traits and communication style for an agent.

    Big Five (OCEAN) floats (0.0-1.0) are internal scoring dimensions used
    for compatibility calculations. Behavioral enums produce natural-language
    labels injected into system prompts that LLMs respond to effectively.

    Attributes:
        traits: Personality trait keywords.
        communication_style: Free-text style description.
        risk_tolerance: Risk tolerance level.
        creativity: Creativity level.
        description: Extended personality description.
        openness: Big Five openness (curiosity, creativity). 0.0-1.0.
        conscientiousness: Big Five conscientiousness (thoroughness). 0.0-1.0.
        extraversion: Big Five extraversion (assertiveness). 0.0-1.0.
        agreeableness: Big Five agreeableness (cooperation). 0.0-1.0.
        stress_response: Emotional stability (1.0 = very calm). 0.0-1.0.
        decision_making: Decision-making approach.
        collaboration: Preferred collaboration mode.
        verbosity: Communication verbosity level.
        conflict_approach: Conflict resolution approach.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    traits: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Personality traits",
    )
    communication_style: NotBlankStr = Field(
        default="neutral",
        max_length=100,
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
        max_length=500,
        description="Extended personality description",
    )

    # Big Five (OCEAN) dimensions — internal scoring only, not prompt-injected.
    openness: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Big Five openness (curiosity, creativity)",
    )
    conscientiousness: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Big Five conscientiousness (thoroughness, reliability)",
    )
    extraversion: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Big Five extraversion (assertiveness, sociability)",
    )
    agreeableness: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Big Five agreeableness (cooperation, empathy)",
    )
    stress_response: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Emotional stability (1.0 = very calm)",
    )

    # Behavioral enums — injected into system prompts as natural-language labels.
    decision_making: DecisionMakingStyle = Field(
        default=DecisionMakingStyle.CONSULTATIVE,
        description="Decision-making approach",
    )
    collaboration: CollaborationPreference = Field(
        default=CollaborationPreference.TEAM,
        description="Preferred collaboration mode",
    )
    verbosity: CommunicationVerbosity = Field(
        default=CommunicationVerbosity.BALANCED,
        description="Communication verbosity level",
    )
    conflict_approach: ConflictApproach = Field(
        default=ConflictApproach.COLLABORATE,
        description="Conflict resolution approach",
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
        provider: LLM provider name (e.g. ``"example-provider"``).
        model_id: Model identifier (e.g. ``"example-medium-001"``).
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

    type: MemoryLevel = Field(
        default=MemoryLevel.SESSION,
        description="Memory persistence type",
    )
    retention_days: int | None = Field(
        default=None,
        ge=1,
        description="Days to retain memories (None = forever)",
    )

    @model_validator(mode="after")
    def _validate_retention_consistency(self) -> Self:
        """Ensure retention_days is None when memory type is MemoryLevel.NONE."""
        if self.type is MemoryLevel.NONE and self.retention_days is not None:
            msg = "retention_days must be None when memory type is 'none'"
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                model="MemoryConfig",
                field="retention_days",
                memory_type=str(self.type),
                retention_days=self.retention_days,
                reason=msg,
            )
            raise ValueError(msg)
        return self


class ToolPermissions(BaseModel):
    """Tool access permissions for an agent.

    Attributes:
        access_level: Tool access level controlling which categories
            are available.
        allowed: Explicitly allowed tool names.
        denied: Explicitly denied tool names.
    """

    model_config = ConfigDict(frozen=True)

    access_level: ToolAccessLevel = Field(
        default=ToolAccessLevel.STANDARD,
        description="Tool access level",
    )
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
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                model="ToolPermissions",
                field="allowed/denied",
                overlap=sorted(overlap),
                reason=msg,
            )
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
        role: Role name (string reference to :class:`~synthorg.core.role.Role`).
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
    autonomy_level: AutonomyLevel | None = Field(
        default=None,
        description="Per-agent autonomy level override (D6)",
    )
    hiring_date: date = Field(description="Date the agent was hired")
    status: AgentStatus = Field(
        default=AgentStatus.ACTIVE,
        description="Current lifecycle status",
    )

    @model_validator(mode="after")
    def _validate_seniority_autonomy(self) -> Self:
        """Reject JUNIOR agents with FULL autonomy (D6)."""
        if (
            self.autonomy_level == AutonomyLevel.FULL
            and self.level == SeniorityLevel.JUNIOR
        ):
            msg = (
                "JUNIOR agents cannot have FULL autonomy — "
                "maximum is SEMI (DESIGN_SPEC D6)"
            )
            raise ValueError(msg)
        return self
