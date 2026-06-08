# module-kind: declarative
"""Template schema: Pydantic models for company templates."""

from collections import Counter
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    ValidationError,
    field_validator,
    model_validator,
)

from synthorg.core.enums import CompanyType, SkillPattern
from synthorg.core.normalization import (
    normalize_ascii_lowercase,
    normalize_identifier,
)
from synthorg.core.types import NotBlankStr
from synthorg.engine.workflow.enums import WorkflowType
from synthorg.hr.seniority import SeniorityLevel
from synthorg.hr.strategy_mode import StrategicOutputMode
from synthorg.memory.config import EmbedderOverrideConfig
from synthorg.observability import get_logger
from synthorg.observability.events.template import TEMPLATE_SCHEMA_VALIDATION_ERROR

logger = get_logger(__name__)


def _default_autonomy() -> dict[str, JsonValue]:
    """Return the default autonomy configuration for a company template.

    Returns:
        A fresh ``{"level": "semi"}`` mapping.
    """
    return {"level": "semi"}


class TemplateVariable(BaseModel):
    """A user-configurable variable within a template.

    Variables declared here are extracted from the template YAML during
    the first parsing pass (before Jinja2 rendering).  The ``variables``
    section must use plain YAML -- no Jinja2 expressions.

    Attributes:
        name: Variable name (used in ``{{ name }}`` placeholders).
        description: Human-readable description for prompts/docs.
        var_type: Expected Python type name.
        default: Default value (``None`` means no default is provided).
            The ``required`` attribute determines whether the user must
            supply a value.
        required: Whether the user must provide this value.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

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
        """Required variables must not define a default.

        Returns:
            The validated model instance (``self``), unchanged.

        Raises:
            ValueError: When a required variable also defines a default.
        """
        if self.required and self.default is not None:
            msg = f"Variable {self.name!r} is required but defines a default"
            logger.warning(TEMPLATE_SCHEMA_VALIDATION_ERROR, error=msg)
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_default_matches_var_type(self) -> Self:
        """Default value type must match ``var_type`` when provided.

        Returns:
            The validated model instance (``self``), unchanged.

        Raises:
            ValueError: When the default value's type is incompatible with
                ``var_type``.
        """
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
        name: Agent name (may contain Jinja2 placeholders).  ``None``
            triggers auto-generation during rendering.
        level: Seniority level override.
        model: Model tier alias (``"large"``, ``"medium"``, ``"small"``)
            or a structured ``ModelRequirement`` dict with ``tier``,
            ``priority``, ``min_context``, and ``capabilities`` fields.
        personality_preset: Named personality preset from the presets registry.
        personality: Inline personality config dict (alternative to
            ``personality_preset``).
        department: Department override (``None`` uses the template
            system default during rendering).
        strategic_output_mode: Strategic output mode override for this
            agent (``StrategicOutputMode | None``).  ``None`` inherits
            the company strategy config default.
        merge_id: Stable identity for inheritance merge.  When a
            template has multiple agents with the same ``(role,
            department)`` pair, ``merge_id`` disambiguates them so
            child templates can target a specific agent.  ``None``
            means no merge_id is set.
        remove: Merge directive -- when ``True``, removes matching
            parent agent during inheritance.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    role: NotBlankStr = Field(description="Built-in role name")
    name: NotBlankStr | None = Field(
        default=None,
        description="Agent name (may have Jinja2 vars); None triggers auto-generation",
    )
    level: SeniorityLevel = Field(
        default=SeniorityLevel.MID,
        description="Seniority level",
    )
    model: NotBlankStr | dict[str, JsonValue] = Field(
        default="medium",
        description="Model tier alias or structured ModelRequirement dict",
    )

    @field_validator("model")
    @classmethod
    def _validate_model(
        cls,
        value: NotBlankStr | dict[str, JsonValue],
    ) -> NotBlankStr | dict[str, JsonValue]:
        """Validate model value: tier string or ModelRequirement dict.

        Returns:
            The validated value, unchanged.

        Raises:
            ValueError: When the value is neither a valid tier string nor
                a parseable model-requirement dict.
        """
        from synthorg.templates.model_requirements import (  # noqa: PLC0415
            parse_model_requirement,
        )

        try:
            parse_model_requirement(value)
        except (ValueError, ValidationError) as exc:
            raise ValueError(str(exc)) from exc
        return value

    personality_preset: NotBlankStr | None = Field(
        default=None,
        description="Named personality preset",
    )
    personality: dict[str, JsonValue] | None = Field(
        default=None,
        description="Inline personality override (alternative to preset)",
    )
    department: NotBlankStr | None = Field(
        default=None,
        description="Department override",
    )
    strategic_output_mode: StrategicOutputMode | None = Field(
        default=None,
        description="Strategic output mode override for this agent",
    )
    merge_id: NotBlankStr | None = Field(
        default=None,
        description="Stable identity for inheritance merge; None means unset",
    )
    remove: bool = Field(
        default=False,
        alias="_remove",
        description="Merge directive: remove matching parent agent",
    )

    @model_validator(mode="after")
    def _validate_personality_mutual_exclusion(self) -> Self:
        """Reject specifying both personality_preset and inline personality.

        Returns:
            The validated model instance (``self``), unchanged.

        Raises:
            ValueError: When both ``personality_preset`` and
                ``personality`` are set.
        """
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

    Provides structural information -- department names, budget
    allocations, the head role, reporting lines, and operational policies.

    Attributes:
        name: Department name (standard or custom).
        budget_percent: Percentage of company budget (0-100).
        head_role: Role name of the department head.
        head_merge_id: Optional ``merge_id`` of the head agent.
            Should be provided when multiple agents share the same
            role used in ``head_role``.
        reporting_lines: Reporting line definitions within this department.
        policies: Department operational policies.
        ceremony_policy: Per-department ceremony policy override
            (``dict[str, JsonValue] | None``).  ``None`` inherits the
            project-level policy.
        remove: Merge directive -- when ``True``, removes matching
            parent department during inheritance.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

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
    head_merge_id: NotBlankStr | None = Field(
        default=None,
        description="merge_id of the head agent for disambiguation",
    )
    reporting_lines: tuple[dict[str, str], ...] = Field(
        default=(),
        description="Reporting line definitions",
    )
    policies: dict[str, JsonValue] | None = Field(
        default=None,
        description="Department operational policies",
    )
    ceremony_policy: dict[str, JsonValue] | None = Field(
        default=None,
        description="Per-department ceremony policy override",
    )
    remove: bool = Field(
        default=False,
        alias="_remove",
        description="Merge directive: remove matching parent department",
    )

    @model_validator(mode="after")
    def _validate_head_merge_id_requires_head_role(self) -> Self:
        """Reject head_merge_id without a corresponding head_role.

        Returns:
            The validated model instance (``self``), unchanged.

        Raises:
            ValueError: When ``head_merge_id`` is set but ``head_role`` is
                missing.
        """
        if self.head_merge_id is not None and self.head_role is None:
            msg = (
                f"Department {self.name!r}: head_merge_id is set "
                f"but head_role is missing"
            )
            logger.warning(TEMPLATE_SCHEMA_VALIDATION_ERROR, error=msg)
            raise ValueError(msg)
        return self


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
        skill_patterns: Skill interaction patterns this template
            exhibits.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    name: NotBlankStr = Field(description="Template display name")
    description: str = Field(default="", description="Template description")
    # Frozen at "1.0.0" -- no template versioning consumers exist yet.
    # Start maintaining when templates are distributed or cached externally.
    version: NotBlankStr = Field(default="1.0.0", description="Semantic version")
    company_type: CompanyType = Field(
        description="Company type this template creates",
    )
    min_agents: int = Field(default=1, ge=1, description="Minimum agents")
    max_agents: int = Field(default=100, ge=1, description="Maximum agents")
    tags: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Categorization tags",
    )
    skill_patterns: tuple[SkillPattern, ...] = Field(
        default=(),
        description="Skill interaction patterns",
    )

    @model_validator(mode="after")
    def _validate_agent_range(self) -> Self:
        """Ensure min_agents <= max_agents.

        Returns:
            The validated model instance (``self``), unchanged.

        Raises:
            ValueError: When ``min_agents`` exceeds ``max_agents``.
        """
        if self.min_agents > self.max_agents:
            msg = f"min_agents ({self.min_agents}) > max_agents ({self.max_agents})"
            logger.warning(TEMPLATE_SCHEMA_VALIDATION_ERROR, error=msg)
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_unique_skill_patterns(self) -> Self:
        """Reject duplicate skill_patterns entries.

        Returns:
            The validated model instance (``self``), unchanged.

        Raises:
            ValueError: When ``skill_patterns`` contains duplicates.
        """
        counts = Counter(self.skill_patterns)
        if len(counts) != len(self.skill_patterns):
            dupes = sorted(sp.value for sp, c in counts.items() if c > 1)
            msg = f"Duplicate skill_patterns: {dupes}"
            logger.warning(TEMPLATE_SCHEMA_VALIDATION_ERROR, error=msg)
            raise ValueError(msg)
        return self


class TemplateMemoryConfig(BaseModel):
    """Template-level memory configuration overrides.

    Attributes:
        embedder: Optional embedder override for the template.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    embedder: EmbedderOverrideConfig | None = Field(
        default=None,
        description="Optional embedder override",
    )


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
        workflow_config: Optional Kanban/Sprint sub-configurations,
            validated as ``WorkflowConfig`` on the rendered ``RootConfig``.
        communication: Communication pattern name.
        budget_monthly: Default monthly budget in the configured currency.
        autonomy: Autonomy configuration dict (e.g. ``{"level": "semi"}``).
        workflow_handoffs: Cross-department workflow handoff definitions.
        escalation_paths: Cross-department escalation path definitions.
        extends: Parent template name for inheritance (``None`` for
            standalone templates).
        memory: Memory configuration overrides (e.g. embedder settings).
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

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
    workflow: WorkflowType = Field(
        default=WorkflowType.AGILE_KANBAN,
        description="Workflow type",
    )
    workflow_config: dict[str, JsonValue] = Field(
        default_factory=dict,
        description=(
            "Optional Kanban/Sprint sub-configurations. "
            "Validated as WorkflowConfig on the rendered RootConfig."
        ),
    )
    communication: NotBlankStr = Field(
        default="hybrid",
        description="Communication pattern",
    )
    budget_monthly: float = Field(
        default=50.0,
        ge=0.0,
        description="Default monthly budget in the configured currency",
    )
    autonomy: dict[str, JsonValue] = Field(
        default_factory=_default_autonomy,
        description="Autonomy configuration",
    )
    workflow_handoffs: tuple[dict[str, JsonValue], ...] = Field(
        default=(),
        description="Cross-department workflow handoffs",
    )
    escalation_paths: tuple[dict[str, JsonValue], ...] = Field(
        default=(),
        description="Cross-department escalation paths",
    )
    extends: NotBlankStr | None = Field(
        default=None,
        description="Parent template name for inheritance",
    )
    uses_packs: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Pack names to compose into this template",
    )
    memory: TemplateMemoryConfig = Field(
        default_factory=TemplateMemoryConfig,
        description="Memory configuration overrides.",
    )

    @field_validator("extends", mode="before")
    @classmethod
    def _normalize_extends(cls, value: object) -> object:
        """Normalize extends to lowercase stripped form.

        Returns:
            The lower-cased value, ``None`` unchanged, or non-string input
            unchanged (left for Pydantic's type validation to reject).
        """
        if value is None:
            return None
        if not isinstance(value, str):
            return value  # let Pydantic's type validation reject it
        return normalize_ascii_lowercase(value)

    @model_validator(mode="after")
    def _validate_agent_count_in_range(self) -> Self:
        """Agent count must be within metadata min/max.

        Skipped when ``extends`` is set because the child may define
        zero agents (inheriting all from parent).  The final merged
        result is validated separately.

        Returns:
            The validated model instance (``self``), unchanged.

        Raises:
            ValueError: When the agent count falls outside the metadata's
                ``min_agents`` / ``max_agents`` range.
        """
        if self.extends is not None or self.uses_packs:
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
        """Variable names must be unique.

        Returns:
            The validated model instance (``self``), unchanged.

        Raises:
            ValueError: When two variables share a name.
        """
        names = [v.name for v in self.variables]
        if len(names) != len(set(names)):
            dupes = sorted(n for n, c in Counter(names).items() if c > 1)
            msg = f"Duplicate variable names: {dupes}"
            logger.warning(TEMPLATE_SCHEMA_VALIDATION_ERROR, error=msg)
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_unique_department_names(self) -> Self:
        """Department names must be unique (case-insensitive).

        Returns:
            The validated model instance (``self``), unchanged.

        Raises:
            ValueError: When two departments share a name
                (case-insensitively).
        """
        names = [normalize_identifier(d.name) for d in self.departments]
        if len(names) != len(set(names)):
            dup_keys = {n for n, c in Counter(names).items() if c > 1}
            dupes = sorted(
                d.name
                for d in self.departments
                if normalize_identifier(d.name) in dup_keys
            )
            msg = f"Duplicate department names: {dupes}"
            logger.warning(TEMPLATE_SCHEMA_VALIDATION_ERROR, error=msg)
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_unique_pack_names(self) -> Self:
        """Pack names in uses_packs must be unique (case-insensitive).

        Returns:
            The validated model instance (``self``), unchanged.

        Raises:
            ValueError: When ``uses_packs`` contains duplicate pack names
                (case-insensitively).
        """
        normalized = [normalize_identifier(p) for p in self.uses_packs]
        if len(normalized) != len(set(normalized)):
            dup_keys = {n for n, c in Counter(normalized).items() if c > 1}
            dupes = sorted(
                p for p in self.uses_packs if normalize_identifier(p) in dup_keys
            )
            msg = f"Duplicate pack names in uses_packs: {dupes}"
            logger.warning(TEMPLATE_SCHEMA_VALIDATION_ERROR, error=msg)
            raise ValueError(msg)
        return self
