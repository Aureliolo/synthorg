"""Agent identity and configuration models."""

import re
from datetime import date
from typing import Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.memory_enums import MemoryCategory, MemoryLevel
from synthorg.core.normalization import normalize_identifier
from synthorg.core.role import Authority, Skill
from synthorg.core.tool_constraints import ToolAccessLevel, ToolSubConstraints
from synthorg.core.types import (
    CAPABILITY_LADDER,
    CapabilityLevel,
    NotBlankStr,
    PersonaLabelStr,
)
from synthorg.hr.enums import AgentStatus
from synthorg.hr.strategy_mode import StrategicOutputMode
from synthorg.observability import get_logger
from synthorg.observability.events.config import CONFIG_VALIDATION_FAILED
from synthorg.ontology.decorator import ontology_entity
from synthorg.security.autonomy.enums import ToolCategory

logger = get_logger(__name__)


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

    An agent binds exactly one ``(provider, model)`` pair, and there is no
    spare. A bare fallback model id would name a model with no connection
    behind it, which is the ambiguity explicit provider binding exists to
    remove; and an agent whose pair is unserviceable is an employee who is
    out, so the org's answer is another agent rather than another model
    behind this one's name.

    Attributes:
        provider: LLM provider name (e.g. ``"example-provider"``).
        model_id: Model identifier (e.g. ``"example-capable-001"``).
        temperature: Sampling temperature (0.0 to 2.0).
        max_tokens: Output ceiling for ONE response, or ``None`` to defer to
            ``engine.agent_max_response_tokens``. Two answers to one question
            is the defect this shape avoids: the agent's own value wins when
            set, the setting answers otherwise, and ``None`` is what tells
            those apart. A flat numeric default here is inherited in silence
            by every agent that states no preference, and a reasoning model
            can exhaust one on hidden reasoning before emitting a single tool
            call.
        capability: What the model can be trusted with
            (``"expert"``/``"capable"``/``"basic"``), set once during model
            matching and never revised: a selection decision reads the model
            catalogue, which is the authority an operator re-grades. Controls
            prompt profile selection.
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
    max_tokens: int | None = Field(
        default=None,
        gt=0,
        description=(
            "Output ceiling for one response; None defers to "
            "engine.agent_max_response_tokens"
        ),
    )
    capability: CapabilityLevel | None = Field(
        default=None,
        description="What the model can be trusted with (expert/capable/basic)",
    )

    @field_validator("model_id")
    @classmethod
    def _reject_capability_literal_as_model(cls, value: str | None) -> str | None:
        """Reject a capability rung used as a concrete model id.

        A rung (``"basic"``/``"capable"``/``"expert"``) is a routing input,
        not a registered model; if one lands in ``model_id`` it is sent
        verbatim to the provider driver and never resolves. ``capability`` is
        the field that carries a rung, so the bad state is caught at
        construction rather than surfacing as a completion-time failure
        downstream.

        Returns:
            The unchanged *value* once confirmed not to be a bare rung.

        Raises:
            ValueError: If *value* is exactly a ``CapabilityLevel`` literal.
        """
        if value is not None and value in CAPABILITY_LADDER:
            msg = (
                f"must be a concrete registered model, not the capability "
                f"{value!r}; set capability for the rung and a real model_id"
            )
            raise ValueError(msg)
        return value


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
        denied_categories: Categories withheld regardless of what the
            access level grants. A name list goes stale the moment a tool
            joins the category, so an identity that must not reach a whole
            class of tool says so by category and stays correct as the
            category grows. ``allowed`` still wins, so one named tool can
            be readmitted from an otherwise withheld category.
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
    denied_categories: tuple[ToolCategory, ...] = Field(
        default=(),
        description="Tool categories withheld regardless of the access level",
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
    containing its role, model backend, memory settings, tool permissions,
    and authority configuration.

    Attributes:
        id: Unique agent identifier.
        name: Agent display name.
        role: Role name (string reference to :class:`~synthorg.core.role.Role`).
        department: Department name (string reference).
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
    # Persona labels, not plain non-blank strings: all three are rendered
    # into the trusted region of a prompt (the persona, the router's
    # candidate list, the decomposition roster), so they are flattened to
    # one line with angle brackets stripped at construction. The render
    # sites flatten again; this stops the value existing in that shape at
    # all.
    name: PersonaLabelStr = Field(description="Agent display name")
    role: PersonaLabelStr = Field(description="Role name")
    department: PersonaLabelStr = Field(description="Department name")
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
