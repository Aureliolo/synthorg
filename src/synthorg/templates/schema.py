"""Template schema: Pydantic models for company templates."""

from collections import Counter
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from synthorg.core.enums import CompanyType, SeniorityLevel
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.observability import get_logger
from synthorg.observability.events.template import TEMPLATE_SCHEMA_VALIDATION_ERROR

logger = get_logger(__name__)


class TemplateVariable(BaseModel):
    """A user-configurable variable within a template.

    Variables declared here are extracted from the template YAML during
    the first parsing pass (before Jinja2 rendering).  The ``variables``
    section must use plain YAML — no Jinja2 expressions.

    Attributes:
        name: Variable name (used in ``{{ name }}`` placeholders).
        description: Human-readable description for prompts/docs.
        var_type: Expected Python type name.
        default: Default value (``None`` means no default is provided).
            The ``required`` attribute determines whether the user must
            supply a value.
        required: Whether the user must provide this value.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: NotBlankStr = Field(description="Variable name")
    description: str = Field(default="", description="Human-readable description")
    var_type: Literal["str", "int", "float", "bool"] = Field(
        default="str",
        description="Expected value type",
    )
    default: str | int | float | bool | None = Field(
        default=None, description="Default value"
    )
    required: bool = Field(default=False, description="Whether required")

    @model_validator(mode="after")
    def _validate_required_has_no_default(self) -> Self:
        """Required variables must not define a default."""
        if self.required and self.default is not None:
            msg = f"Variable {self.name!r} is required but defines a default"
            logger.warning(TEMPLATE_SCHEMA_VALIDATION_ERROR, error=msg)
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_default_matches_var_type(self) -> Self:
        """Default value type must match ``var_type`` when provided."""
        if self.default is None:
            return self
        # Reject bools explicitly for numeric types because
        # ``isinstance(True, int)`` is ``True`` in Python.
        if isinstance(self.default, bool) and self.var_type in ("int", "float"):
            msg = (
                f"Variable {self.name!r}: default {self.default!r} "
                f"is not compatible with var_type {self.var_type!r}"
            )
            logger.warning(TEMPLATE_SCHEMA_VALIDATION_ERROR, error=msg)
            raise ValueError(msg)
        type_map: dict[str, type | tuple[type, ...]] = {
            "str": str,
            "int": int,
            "float": (int, float),
            "bool": bool,
        }
        expected = type_map[self.var_type]
        if not isinstance(self.default, expected):
            msg = (
                f"Variable {self.name!r}: default {self.default!r} "
                f"is not compatible with var_type {self.var_type!r}"
            )
            logger.warning(TEMPLATE_SCHEMA_VALIDATION_ERROR, error=msg)
            raise ValueError(msg)  # noqa: TRY004
        return self


class TemplateAgentConfig(BaseModel):
    """Agent definition within a template.

    Uses string references and presets rather than full ``AgentConfig``.
    The renderer expands these into full agent configuration dicts.

    Attributes:
        role: Built-in role name (case-insensitive match to role catalog).
        name: Agent name (may contain Jinja2 placeholders; empty triggers
            auto-generation).
        level: Seniority level override.
        model: Model tier alias (e.g. ``"large"``, ``"medium"``, ``"small"``).
        personality_preset: Named personality preset from the presets registry.
        personality: Inline personality config dict (alternative to
            ``personality_preset``).
        department: Department override (``None`` uses the template
            system default during rendering).
        merge_id: Stable identity for inheritance merge.  When a
            template has multiple agents with the same ``(role,
            department)`` pair, ``merge_id`` disambiguates them so
            child templates can target a specific agent.
        remove: Merge directive — when ``True``, removes matching
            parent agent during inheritance.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    role: NotBlankStr = Field(description="Built-in role name")
    name: str = Field(default="", description="Agent name (may have Jinja2 vars)")
    level: SeniorityLevel = Field(
        default=SeniorityLevel.MID,
        description="Seniority level",
    )
    model: NotBlankStr = Field(default="medium", description="Model tier alias")
    personality_preset: NotBlankStr | None = Field(
        default=None,
        description="Named personality preset",
    )
    personality: dict[str, Any] | None = Field(
        default=None,
        description="Inline personality override (alternative to preset)",
    )
    department: NotBlankStr | None = Field(
        default=None,
        description="Department override",
    )
    merge_id: str = Field(
        default="",
        description="Stable identity for inheritance merge",
    )
    remove: bool = Field(
        default=False,
        alias="_remove",
        description="Merge directive: remove matching parent agent",
    )

    @model_validator(mode="after")
    def _validate_personality_mutual_exclusion(self) -> Self:
        """Reject specifying both personality_preset and inline personality."""
        if self.personality_preset is not None and self.personality is not None:
            msg = (
                "Cannot specify both 'personality_preset' and 'personality'. "
                "Use one or the other."
            )
            logger.warning(TEMPLATE_SCHEMA_VALIDATION_ERROR, error=msg)
            raise ValueError(msg)
        return self


class TemplateDepartmentConfig(BaseModel):
    """Department definition within a template.

    Provides structural information — department names, budget
    allocations, the head role, reporting lines, and operational policies.

    Attributes:
        name: Department name (standard or custom).
        budget_percent: Percentage of company budget (0-100).
        head_role: Role name of the department head.
        reporting_lines: Reporting line definitions within this department.
        policies: Department operational policies.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: NotBlankStr = Field(description="Department name")
    budget_percent: float = Field(
        default=0.0,
        ge=0.0,
        le=100.0,
        description="Percentage of company budget",
    )
    head_role: NotBlankStr | None = Field(
        default=None,
        description="Role name of department head",
    )
    reporting_lines: tuple[dict[str, str], ...] = Field(
        default=(),
        description="Reporting line definitions",
    )
    policies: dict[str, Any] | None = Field(
        default=None,
        description="Department operational policies",
    )


class TemplateMetadata(BaseModel):
    """Metadata about a company template.

    Attributes:
        name: Template display name.
        description: What this template is for.
        version: Semantic version string.
        company_type: Which ``CompanyType`` this template creates.
        min_agents: Minimum number of agents.
        max_agents: Maximum number of agents.
        tags: Categorization tags.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: NotBlankStr = Field(description="Template display name")
    description: str = Field(default="", description="Template description")
    version: NotBlankStr = Field(default="1.0.0", description="Semantic version")
    company_type: CompanyType = Field(
        description="Company type this template creates",
    )
    min_agents: int = Field(default=1, ge=1, description="Minimum agents")
    max_agents: int = Field(default=100, ge=1, description="Maximum agents")
    tags: tuple[NotBlankStr, ...] = Field(default=(), description="Categorization tags")

    @model_validator(mode="after")
    def _validate_agent_range(self) -> Self:
        """Ensure min_agents <= max_agents."""
        if self.min_agents > self.max_agents:
            msg = f"min_agents ({self.min_agents}) > max_agents ({self.max_agents})"
            logger.warning(TEMPLATE_SCHEMA_VALIDATION_ERROR, error=msg)
            raise ValueError(msg)
        return self


class CompanyTemplate(BaseModel):
    """A complete company template definition.

    This is the top-level model parsed from a template YAML file
    during the first pass (before Jinja2 rendering).  It holds
    metadata, variable declarations, and the structural definitions
    for agents and departments.

    The raw YAML text is stored separately by the loader for the
    second pass (Jinja2 rendering).

    Attributes:
        metadata: Template metadata.
        variables: Declared template variables (plain YAML, no Jinja2).
        agents: Template agent definitions.
        departments: Template department definitions.
        workflow: Workflow name.
        communication: Communication pattern name.
        budget_monthly: Default monthly budget in USD.
        autonomy: Autonomy level (0.0 = full human oversight,
            1.0 = fully autonomous).
        workflow_handoffs: Cross-department workflow handoff definitions.
        escalation_paths: Cross-department escalation path definitions.
        extends: Parent template name for inheritance (``None`` for
            standalone templates).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    metadata: TemplateMetadata = Field(description="Template metadata")
    variables: tuple[TemplateVariable, ...] = Field(
        default=(),
        description="Declared template variables",
    )
    agents: tuple[TemplateAgentConfig, ...] = Field(
        description="Template agent definitions",
    )
    departments: tuple[TemplateDepartmentConfig, ...] = Field(
        default=(),
        description="Template department definitions",
    )
    workflow: NotBlankStr = Field(
        default="agile_kanban",
        description="Workflow name",
    )
    communication: NotBlankStr = Field(
        default="hybrid",
        description="Communication pattern",
    )
    budget_monthly: float = Field(
        default=50.0,
        ge=0.0,
        description="Default monthly budget in USD",
    )
    autonomy: float = Field(
        default=0.5,
        ge=0.0,
        le=1.0,
        description="Autonomy level",
    )
    workflow_handoffs: tuple[dict[str, Any], ...] = Field(
        default=(),
        description="Cross-department workflow handoffs",
    )
    escalation_paths: tuple[dict[str, Any], ...] = Field(
        default=(),
        description="Cross-department escalation paths",
    )
    extends: NotBlankStr | None = Field(
        default=None,
        description="Parent template name for inheritance",
    )

    @field_validator("extends", mode="before")
    @classmethod
    def _normalize_extends(cls, value: Any) -> Any:
        """Normalize extends to lowercase stripped form."""
        if value is None:
            return None
        if not isinstance(value, str):
            return value  # let Pydantic's type validation reject it
        return value.strip().lower()

    @model_validator(mode="after")
    def _validate_agent_count_in_range(self) -> Self:
        """Agent count must be within metadata min/max.

        Skipped when ``extends`` is set because the child may define
        zero agents (inheriting all from parent).  The final merged
        result is validated separately.
        """
        if self.extends is not None:
            return self
        count = len(self.agents)
        if count < self.metadata.min_agents:
            msg = (
                f"Template defines {count} agent(s), "
                f"minimum is {self.metadata.min_agents}"
            )
            logger.warning(TEMPLATE_SCHEMA_VALIDATION_ERROR, error=msg)
            raise ValueError(msg)
        if count > self.metadata.max_agents:
            msg = (
                f"Template defines {count} agent(s), "
                f"maximum is {self.metadata.max_agents}"
            )
            logger.warning(TEMPLATE_SCHEMA_VALIDATION_ERROR, error=msg)
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_unique_variable_names(self) -> Self:
        """Variable names must be unique."""
        names = [v.name for v in self.variables]
        if len(names) != len(set(names)):
            dupes = sorted(n for n, c in Counter(names).items() if c > 1)
            msg = f"Duplicate variable names: {dupes}"
            logger.warning(TEMPLATE_SCHEMA_VALIDATION_ERROR, error=msg)
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_unique_department_names(self) -> Self:
        """Department names must be unique (case-insensitive)."""
        names = [d.name.strip().casefold() for d in self.departments]
        if len(names) != len(set(names)):
            dup_keys = {n for n, c in Counter(names).items() if c > 1}
            dupes = sorted(
                d.name
                for d in self.departments
                if d.name.strip().casefold() in dup_keys
            )
            msg = f"Duplicate department names: {dupes}"
            logger.warning(TEMPLATE_SCHEMA_VALIDATION_ERROR, error=msg)
            raise ValueError(msg)
        return self
