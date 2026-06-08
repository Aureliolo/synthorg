"""Role and skill domain models."""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from synthorg.core.types import (
    ModelTier,
    NotBlankStr,
    validate_unique_strings,
)
from synthorg.hr.seniority import SeniorityLevel
from synthorg.ontology.decorator import ontology_entity
from synthorg.organization.enums import DepartmentName


class Skill(BaseModel):
    """Structured capability description, A2A AgentSkill-aligned.

    Mirrors the A2A protocol ``AgentSkill`` shape so projection to
    ``A2AAgentSkill`` is lossless.  ``proficiency`` is the SynthOrg-specific
    addition used for quality-aware routing ("route to the agent with the
    highest Python proficiency").

    Attributes:
        id: Unique skill identifier (e.g. ``"code-review"``).
        name: Human-readable display name (e.g. ``"Code Review"``).
        description: Capability description for semantic matching.
        tags: Searchable tags for multi-faceted routing.
        input_modes: MIME types the agent accepts for this skill.
        output_modes: MIME types the agent produces for this skill.
        proficiency: Proficiency level in ``[0.0, 1.0]``.  Default ``1.0``
            preserves legacy boolean-match scoring when proficiency is
            unspecified.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    id: NotBlankStr = Field(description="Unique skill identifier")
    name: NotBlankStr = Field(description="Human-readable display name")
    description: str = Field(
        default="",
        description="Capability description for semantic matching",
    )
    tags: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Searchable tags for multi-faceted routing",
    )
    input_modes: tuple[NotBlankStr, ...] = Field(
        default=("text/plain",),
        description="MIME types the agent accepts for this skill",
    )
    output_modes: tuple[NotBlankStr, ...] = Field(
        default=("text/plain",),
        description="MIME types the agent produces for this skill",
    )
    proficiency: float = Field(
        default=1.0,
        ge=0.0,
        le=1.0,
        description="Proficiency level in [0.0, 1.0]",
    )

    @field_validator("tags", "input_modes", "output_modes")
    @classmethod
    def _reject_duplicate_entries(
        cls,
        value: tuple[str, ...],
        info: object,
    ) -> tuple[str, ...]:
        """Reject duplicate entries within a tuple field.

        Duplicates are silently nonsensical (e.g. the same tag listed twice)
        and would confuse routing dedup logic downstream.  Reject at
        construction so the issue surfaces in the API response rather than
        as a subtle ranking bug.

        Returns:
            The unchanged *value* tuple once uniqueness is confirmed.

        Raises:
            ValueError: If *value* contains any duplicate entry.
        """
        field_name = getattr(info, "field_name", "value")
        validate_unique_strings(value, field_name)
        return value


class Authority(BaseModel):
    """Authority scope for an agent or role.

    Attributes:
        can_approve: Task types this role can approve.
        reports_to: Role this position reports to.
        can_delegate_to: Roles this position can delegate tasks to.
        budget_limit: Maximum spend per task in base currency units.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    can_approve: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Task types this role can approve",
    )
    reports_to: NotBlankStr | None = Field(
        default=None,
        description="Role this position reports to",
    )
    can_delegate_to: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Roles this position can delegate tasks to",
    )
    budget_limit: float = Field(
        default=0.0,
        ge=0.0,
        description="Maximum spend per task in base currency units",
    )


class SeniorityInfo(BaseModel):
    """Mapping from seniority level to authority and model configuration.

    Attributes:
        level: The seniority level.
        authority_scope: Description of authority at this level.
        typical_model_tier: Recommended model tier (e.g. ``"large"``).
        cost_tier: Cost tier identifier (built-in ``CostTier`` or user-defined string).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    level: SeniorityLevel = Field(description="Seniority level")
    authority_scope: NotBlankStr = Field(
        description="Description of authority at this level",
    )
    typical_model_tier: ModelTier = Field(
        description="Recommended model tier",
    )
    cost_tier: NotBlankStr = Field(
        description="Cost tier identifier (built-in or user-defined)",
    )


@ontology_entity
class Role(BaseModel):
    """A job definition within the organization.

    Attributes:
        name: Role name (e.g. ``"Backend Developer"``).
        department: Department this role belongs to.
        required_skills: Skills required for this role.
        authority_level: Default seniority level.
        tool_access: Tools available to this role.
        system_prompt_template: Template file for system prompt.
        description: Human-readable description.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(description="Role name")
    department: DepartmentName = Field(
        description="Department this role belongs to",
    )
    required_skills: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Skills required for this role",
    )
    authority_level: SeniorityLevel = Field(
        default=SeniorityLevel.MID,
        description="Default seniority level for this role",
    )
    tool_access: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Tools available to this role",
    )
    system_prompt_template: NotBlankStr | None = Field(
        default=None,
        description="Template file for system prompt",
    )
    description: str = Field(
        default="",
        description="Human-readable description",
    )


class CustomRole(BaseModel):
    """User-defined custom role via configuration.

    Unlike :class:`Role`, the ``department`` field accepts arbitrary strings
    in addition to :class:`~synthorg.organization.enums.DepartmentName` values,
    allowing users to define roles in non-standard departments.

    Attributes:
        name: Custom role name.
        department: Department (standard or custom name).
        required_skills: Required skills for this role.
        system_prompt_template: Template file for system prompt.
        authority_level: Default seniority level.
        suggested_model: Suggested model tier.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(description="Custom role name")
    department: DepartmentName | str = Field(
        description="Department (standard or custom name)",
    )
    required_skills: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Required skills for this role",
    )
    system_prompt_template: NotBlankStr | None = Field(
        default=None,
        description="Template file for system prompt",
    )
    authority_level: SeniorityLevel = Field(
        default=SeniorityLevel.MID,
        description="Default seniority level",
    )
    suggested_model: NotBlankStr | None = Field(
        default=None,
        description="Suggested model tier",
    )

    @field_validator("department")
    @classmethod
    def _department_not_empty(cls, v: DepartmentName | str) -> DepartmentName | str:
        """Ensure department is not empty and strip surrounding whitespace.

        Returns:
            The ``DepartmentName`` unchanged, or the whitespace-stripped
            department string.

        Raises:
            ValueError: If the department string is empty or
                whitespace-only.
        """
        if isinstance(v, DepartmentName):
            return v
        stripped = v.strip()
        if not stripped:
            msg = "Department name must not be empty"
            raise ValueError(msg)
        return stripped
