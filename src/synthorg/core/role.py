"""Role and skill domain models."""

from pydantic import BaseModel, ConfigDict, Field, field_validator

from synthorg.core.enums import (
    DepartmentName,
    ProficiencyLevel,
    SeniorityLevel,
    SkillCategory,
)
from synthorg.core.types import NotBlankStr  # noqa: TC001


class Skill(BaseModel):
    """A capability an agent possesses.

    Attributes:
        name: Skill name (e.g. ``"python"``, ``"system-design"``).
        category: Broad skill category.
        proficiency: Agent's proficiency in this skill.
    """

    model_config = ConfigDict(frozen=True)

    name: NotBlankStr = Field(description="Skill name")
    category: SkillCategory = Field(description="Skill category")
    proficiency: ProficiencyLevel = Field(
        default=ProficiencyLevel.INTERMEDIATE,
        description="Proficiency level",
    )


class Authority(BaseModel):
    """Authority scope for an agent or role.

    Attributes:
        can_approve: Task types this role can approve.
        reports_to: Role this position reports to.
        can_delegate_to: Roles this position can delegate tasks to.
        budget_limit: Maximum USD spend per task.
    """

    model_config = ConfigDict(frozen=True)

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
        description="Maximum USD per task",
    )


class SeniorityInfo(BaseModel):
    """Mapping from seniority level to authority and model configuration.

    Attributes:
        level: The seniority level.
        authority_scope: Description of authority at this level.
        typical_model_tier: Recommended model tier (e.g. ``"large"``).
        cost_tier: Cost tier identifier (built-in ``CostTier`` or user-defined string).
    """

    model_config = ConfigDict(frozen=True)

    level: SeniorityLevel = Field(description="Seniority level")
    authority_scope: NotBlankStr = Field(
        description="Description of authority at this level",
    )
    typical_model_tier: NotBlankStr = Field(
        description="Recommended model tier",
    )
    cost_tier: NotBlankStr = Field(
        description="Cost tier identifier (built-in or user-defined)",
    )


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

    model_config = ConfigDict(frozen=True)

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
    in addition to :class:`~synthorg.core.enums.DepartmentName` values,
    allowing users to define roles in non-standard departments.

    Attributes:
        name: Custom role name.
        department: Department (standard or custom name).
        required_skills: Required skills for this role.
        system_prompt_template: Template file for system prompt.
        authority_level: Default seniority level.
        suggested_model: Suggested model tier.
    """

    model_config = ConfigDict(frozen=True)

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
        """Ensure department is not empty and strip surrounding whitespace."""
        if isinstance(v, DepartmentName):
            return v
        stripped = v.strip()
        if not stripped:
            msg = "Department name must not be empty"
            raise ValueError(msg)
        return stripped
