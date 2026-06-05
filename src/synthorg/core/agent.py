"""Agent identity and configuration models."""

import re
from datetime import date
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
    MemoryCategory,
    MemoryLevel,
    RiskTolerance,
    SeniorityLevel,
    StrategicOutputMode,
    ToolAccessLevel,
)
from synthorg.core.normalization import normalize_identifier
from synthorg.core.role import Authority, Skill
from synthorg.core.tool_constraints import ToolSubConstraints
from synthorg.core.types import ModelTier, NotBlankStr
from synthorg.observability import get_logger
from synthorg.observability.events.config import CONFIG_VALIDATION_FAILED
from synthorg.ontology.decorator import ontology_entity

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

    # Big Five (OCEAN) dimensions -- internal scoring only, not prompt-injected.
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

    # Behavioral enums -- injected into system prompts as natural-language labels.
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
        primary: Core competency skills.
        secondary: Supporting skills.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    primary: tuple[Skill, ...] = Field(
        default=(),
        description="Primary skills",
    )
    secondary: tuple[Skill, ...] = Field(
        default=(),
        description="Secondary skills",
    )

    @model_validator(mode="after")
    def _validate_unique_ids(self) -> Self:
        """Reject duplicate skill IDs within or across tiers.

        Downstream consumers (routing scorer, A2A projection) build
        ``{skill.id: skill}`` dicts and would silently drop all but one
        entry when duplicates exist.  Reject at construction so
        ambiguous configurations surface as validation errors instead of
        order-dependent ranking artifacts.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If any skill ID is duplicated within or across
                the primary and secondary tiers.
        """
        primary_ids = [s.id for s in self.primary]
        secondary_ids = [s.id for s in self.secondary]
        primary_dupes = sorted(
            {sid for sid in primary_ids if primary_ids.count(sid) > 1}
        )
        if primary_dupes:
            msg = f"Duplicate skill ids in primary tier: {primary_dupes}"
            raise ValueError(msg)
        secondary_dupes = sorted(
            {sid for sid in secondary_ids if secondary_ids.count(sid) > 1}
        )
        if secondary_dupes:
            msg = f"Duplicate skill ids in secondary tier: {secondary_dupes}"
            raise ValueError(msg)
        overlap = set(primary_ids) & set(secondary_ids)
        if overlap:
            msg = (
                f"Skills cannot appear in both primary and secondary tiers: "
                f"{sorted(overlap)}"
            )
            raise ValueError(msg)
        return self


class ModelConfig(BaseModel):
    """LLM model configuration for an agent.

    Attributes:
        provider: LLM provider name (e.g. ``"example-provider"``).
        model_id: Model identifier (e.g. ``"example-medium-001"``).
        temperature: Sampling temperature (0.0 to 2.0).
        max_tokens: Maximum output tokens.
        fallback_model: Optional fallback model identifier.
        model_tier: Capability tier (``"large"``/``"medium"``/``"small"``)
            set during model matching and updated by budget auto-downgrade.
            Controls prompt profile selection.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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
    model_tier: ModelTier | None = Field(
        default=None,
        description="Model capability tier (large/medium/small)",
    )


class AgentRetentionRule(BaseModel):
    """Per-category retention override for an agent.

    Structurally identical to
    :class:`~synthorg.memory.consolidation.models.RetentionRule` but
    defined in ``core`` to avoid a ``core -> memory`` import dependency.

    Attributes:
        category: Memory category this override applies to.
        retention_days: Number of days to retain memories in this
            category.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    category: MemoryCategory = Field(
        description="Memory category this override applies to",
    )
    retention_days: int = Field(
        ge=1,
        description="Number of days to retain memories",
    )


class MemoryConfig(BaseModel):
    """Memory configuration for an agent.

    Attributes:
        type: Memory persistence type.
        retention_days: Days to retain memories (``None`` means forever).
            Also serves as the agent-level global default for retention
            when per-category overrides are not specified.
        retention_overrides: Per-category retention overrides for this
            agent.  These take priority over company-level per-category
            rules during retention enforcement.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    type: MemoryLevel = Field(
        default=MemoryLevel.SESSION,
        description="Memory persistence type",
    )
    retention_days: int | None = Field(
        default=None,
        ge=1,
        description="Days to retain memories (None = forever)",
    )
    retention_overrides: tuple[AgentRetentionRule, ...] = Field(
        default=(),
        description="Per-category retention overrides for this agent",
    )

    @model_validator(mode="after")
    def _validate_retention_consistency(self) -> Self:
        """Ensure retention fields are unset when memory type is NONE.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If ``retention_days`` or ``retention_overrides``
                are set while the memory type is ``NONE``.
        """
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
        if self.type is MemoryLevel.NONE and self.retention_overrides:
            msg = "retention_overrides must be empty when memory type is 'none'"
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                model="MemoryConfig",
                field="retention_overrides",
                memory_type=str(self.type),
                reason=msg,
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_unique_override_categories(self) -> Self:
        """Ensure each category appears at most once in overrides.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If any retention-override category is duplicated.
        """
        categories = [rule.category for rule in self.retention_overrides]
        if len(categories) != len(set(categories)):
            seen: set[MemoryCategory] = set()
            dupe_values: set[str] = set()
            for c in categories:
                if c in seen:
                    dupe_values.add(c.value)
                seen.add(c)
            dupes = sorted(dupe_values)
            msg = f"Duplicate retention override categories: {dupes}"
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                model="MemoryConfig",
                field="retention_overrides",
                duplicates=dupes,
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
        mcp_capabilities: MCP capability patterns controlling which
            internal MCP tools the agent can see.  Supports wildcards
            (e.g. ``"tasks:*"``, ``"*:read"``, ``"*"``).
        sub_constraints: Optional per-agent sub-constraints overriding
            the access level defaults.  When ``None``, the checker
            resolves defaults from the access level.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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
    mcp_capabilities: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="MCP capability patterns (e.g. 'tasks:read', 'agents:*')",
    )
    sub_constraints: ToolSubConstraints | None = Field(
        default=None,
        description="Per-agent sub-constraint overrides",
    )

    @model_validator(mode="after")
    def _validate_no_overlap(self) -> Self:
        """Ensure no tool appears in both allowed and denied lists.

        Comparison is case-insensitive.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If any tool appears in both the allowed and
                denied lists (case-insensitive).
        """
        allowed_normalized = {normalize_identifier(t) for t in self.allowed}
        denied_normalized = {normalize_identifier(t) for t in self.denied}
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

    @model_validator(mode="after")
    def _validate_mcp_capability_format(self) -> Self:
        """Validate MCP capability pattern format.

        Accepted formats: ``"domain:action"``, ``"domain:*"``,
        ``"*:action"``, ``"*"``.  Components must be lowercase
        alphanumeric with underscores.

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If any MCP capability does not match the accepted
                ``domain:action`` pattern grammar.
        """
        pattern = re.compile(r"^(?:\*|[a-z][a-z0-9_]*):(?:\*|[a-z][a-z0-9_]*)$|^\*$")
        for cap in self.mcp_capabilities:
            if not pattern.match(normalize_identifier(cap)):
                msg = (
                    f"Invalid MCP capability pattern: {cap!r}. "
                    f"Expected 'domain:action', 'domain:*', '*:action', or '*'"
                )
                logger.warning(
                    CONFIG_VALIDATION_FAILED,
                    model="ToolPermissions",
                    field="mcp_capabilities",
                    pattern=cap,
                    reason=msg,
                )
                raise ValueError(msg)
        return self


@ontology_entity
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
        autonomy_level: Per-agent autonomy level override (``None`` uses
            department/company default).
        strategic_output_mode: Per-agent strategic output mode override
            (``None`` inherits company strategy config default).
        hiring_date: Date the agent was hired.
        status: Current lifecycle status.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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
    strategic_output_mode: StrategicOutputMode | None = Field(
        default=None,
        description=(
            "Per-agent strategic output mode override. "
            "None inherits the company strategy config default."
        ),
    )
    hiring_date: date = Field(description="Date the agent was hired")
    status: AgentStatus = Field(
        default=AgentStatus.ACTIVE,
        description="Current lifecycle status",
    )

    @model_validator(mode="after")
    def _validate_seniority_autonomy(self) -> Self:
        """Reject JUNIOR agents with FULL autonomy (D6).

        Returns:
            The validated instance (Pydantic ``model_validator`` contract).

        Raises:
            ValueError: If a JUNIOR-level agent is configured with FULL
                autonomy (the spec caps JUNIOR at SEMI).
        """
        if (
            self.autonomy_level == AutonomyLevel.FULL
            and self.level == SeniorityLevel.JUNIOR
        ):
            msg = (
                "JUNIOR agents cannot have FULL autonomy -- "
                "maximum is SEMI (DESIGN_SPEC D6)"
            )
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                model="AgentIdentity",
                field="autonomy_level/level",
                reason=msg,
            )
            raise ValueError(msg)
        return self
