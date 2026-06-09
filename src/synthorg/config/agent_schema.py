# module-kind: code
"""Agent, routing, and runtime-behaviour config models.

Config-level Pydantic models consumed by :class:`RootConfig` fields:
per-agent configuration, model routing rules, graceful shutdown, and
task assignment.
"""

from typing import ClassVar, Literal, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from synthorg.core.autonomy_enums import AutonomyLevel
from synthorg.core.types import NotBlankStr, stable_agent_id
from synthorg.hr.seniority import SeniorityLevel
from synthorg.hr.strategy_mode import StrategicOutputMode
from synthorg.observability import get_logger
from synthorg.observability.events.config import CONFIG_VALIDATION_FAILED
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import MirrorField, apply_settings_mirrors

logger = get_logger(__name__)


class RoutingRuleConfig(BaseModel):
    """A single model routing rule.

    At least one of ``role_level`` or ``task_type`` must be set so the
    rule can match incoming requests.

    Attributes:
        role_level: Seniority level this rule applies to.
        task_type: Task type this rule applies to.
        preferred_model: Preferred model alias or ID.
        fallback: Fallback model alias or ID.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    role_level: SeniorityLevel | None = Field(
        default=None,
        description="Seniority level filter",
    )
    task_type: NotBlankStr | None = Field(
        default=None,
        description="Task type filter",
    )
    preferred_model: NotBlankStr = Field(
        description="Preferred model alias or ID",
    )
    fallback: NotBlankStr | None = Field(
        default=None,
        description="Fallback model alias or ID",
    )

    @model_validator(mode="after")
    def _at_least_one_matcher(self) -> Self:
        if self.role_level is None and self.task_type is None:
            msg = "Routing rule must specify at least role_level or task_type"
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                model="RoutingRuleConfig",
                error=msg,
                role_level=self.role_level,
                task_type=self.task_type,
                preferred_model=self.preferred_model,
                fallback=self.fallback,
            )
            raise ValueError(msg)
        return self


class RoutingConfig(BaseModel):
    """Model routing configuration.

    Attributes:
        strategy: Routing strategy name (e.g. ``"cost_aware"``).
        rules: Ordered routing rules.
        fallback_chain: Ordered fallback model aliases or IDs.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    _MIRROR_FIELDS: ClassVar[tuple[MirrorField, ...]] = (
        MirrorField(
            field="strategy",
            namespace=SettingNamespace.PROVIDERS,
            key="routing_strategy",
        ),
    )

    strategy: NotBlankStr = Field(
        default="cost_aware",
        description="Routing strategy name",
    )
    rules: tuple[RoutingRuleConfig, ...] = Field(
        default=(),
        description="Ordered routing rules",
    )
    fallback_chain: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Ordered fallback model aliases or IDs",
    )

    @model_validator(mode="before")
    @classmethod
    def _apply_mirrors(cls, data: object) -> object:
        return apply_settings_mirrors(data, cls._MIRROR_FIELDS)


class AgentConfig(BaseModel):
    """Agent configuration from YAML.

    Personality, model, memory, tools, and authority stay raw dicts so
    wizard-emitted intermediate keys (e.g. a resolved ``tier``) round-trip
    through validation that ``extra="forbid"`` sub-models would reject; the
    engine rehydrates each into its typed form at startup.

    Attributes:
        id: Stable agent id derived deterministically from ``name``.
        name: Agent display name.
        role: Role name.
        department: Department name.
        level: Seniority level.
        personality: Raw personality config dict.
        model: Raw model config dict.
        memory: Raw memory config dict.
        tools: Raw tools config dict.
        authority: Raw authority config dict.
        autonomy_level: Per-agent autonomy level override
            (``None`` inherits default).
        strategic_output_mode: Per-agent strategic output mode override
            (``StrategicOutputMode | None``).  ``None`` inherits the
            company strategy config default.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    # The before-validator overwrites this with stable_agent_id(name) on
    # every name-bearing construction, so the uuid4 factory is a vestigial
    # placeholder that keeps the field non-required for mypy; it is not a
    # real fallback in normal operation.
    id: UUID = Field(
        default_factory=uuid4,
        description="Stable agent id, derived deterministically from the name.",
    )
    name: NotBlankStr = Field(description="Agent display name")
    role: NotBlankStr = Field(description="Role name")
    department: NotBlankStr = Field(description="Department name")
    level: SeniorityLevel = Field(
        default=SeniorityLevel.MID,
        description="Seniority level",
    )
    personality_preset: NotBlankStr | None = Field(
        default=None,
        description="Named personality preset; round-trips from template setup.",
    )
    personality: dict[str, JsonValue] = Field(
        default_factory=dict,
        description="Raw personality config",
    )
    model: dict[str, JsonValue] = Field(
        default_factory=dict,
        description="Raw model config",
    )
    memory: dict[str, JsonValue] = Field(
        default_factory=dict,
        description="Raw memory config",
    )
    tools: dict[str, JsonValue] = Field(
        default_factory=dict,
        description="Raw tools config",
    )
    authority: dict[str, JsonValue] = Field(
        default_factory=dict,
        description="Raw authority config",
    )
    autonomy_level: AutonomyLevel | None = Field(
        default=None,
        description="Per-agent autonomy level override; None inherits the default.",
    )
    strategic_output_mode: StrategicOutputMode | None = Field(
        default=None,
        description=(
            "Per-agent strategic output mode override. "
            "None inherits the company strategy config default."
        ),
    )
    tier: Literal["large", "medium", "small"] | None = Field(
        default=None,
        description="Resolved model tier from the setup wizard; round-trips.",
    )
    model_requirement: dict[str, JsonValue] | None = Field(
        default=None,
        description=(
            "Raw model requirement dict from the setup wizard; kept raw to "
            "avoid a config -> templates import cycle."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _derive_stable_id(cls, data: object) -> object:
        """Force ``id`` to ``stable_agent_id(name)`` before validation.

        A declared field (unlike a computed one) round-trips through the
        settings write-then-read cycle under ``extra="forbid"``.

        Returns:
            *data* with a canonical ``id`` when name-bearing, else unchanged.
        """
        if isinstance(data, dict) and data.get("name"):
            return {**data, "id": str(stable_agent_id(str(data["name"])))}
        return data


class GracefulShutdownConfig(BaseModel):
    """Configuration for graceful shutdown behaviour.

    Attributes:
        strategy: Shutdown strategy name (``"cooperative_timeout"``,
            ``"immediate"``, ``"finish_tool"``, or ``"checkpoint"``).
        grace_seconds: Seconds to wait for cooperative agent exit
            before force-cancelling.
        cleanup_seconds: Seconds allowed for cleanup callbacks
            (persist costs, close connections, flush logs).
        tool_timeout_seconds: Per-tool timeout for the
            ``"finish_tool"`` strategy (seconds).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    strategy: Literal[
        "cooperative_timeout", "immediate", "finish_tool", "checkpoint"
    ] = Field(
        default="cooperative_timeout",
        description="Shutdown strategy name",
    )
    grace_seconds: float = Field(
        default=30.0,
        gt=0,
        le=300,
        description="Seconds to wait for cooperative agent exit",
    )
    cleanup_seconds: float = Field(
        default=5.0,
        gt=0,
        le=60,
        description="Seconds allowed for cleanup callbacks",
    )
    tool_timeout_seconds: float = Field(
        default=60.0,
        gt=0,
        le=300,
        description="Per-tool timeout for finish_tool strategy",
    )


class TaskAssignmentConfig(BaseModel):
    """Configuration for task assignment behaviour.

    Attributes:
        strategy: Assignment strategy name (e.g. ``"role_based"``).
        min_score: Minimum capability score for agent eligibility.
        max_concurrent_tasks_per_agent: Maximum tasks an agent can
            handle concurrently. Enforced by scoring-based strategies
            that filter out agents at capacity.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    # Known strategy names -- must stay in sync with
    # ``STRATEGY_NAME_*`` constants in ``engine.assignment.strategies``.
    # ``"hierarchical"`` requires a ``HierarchyResolver`` at runtime.
    _VALID_STRATEGIES: ClassVar[frozenset[str]] = frozenset(
        {
            "manual",
            "role_based",
            "load_balanced",
            "cost_optimized",
            "hierarchical",
            "auction",
        },
    )

    strategy: NotBlankStr = Field(
        default="role_based",
        description="Assignment strategy name",
    )
    min_score: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description="Minimum capability score for agent eligibility",
    )
    max_concurrent_tasks_per_agent: int = Field(
        default=5,
        ge=1,
        le=50,
        description=(
            "Maximum concurrent tasks an agent is intended to handle. "
            "Enforced by scoring-based strategies that filter out "
            "agents at capacity."
        ),
    )

    @model_validator(mode="after")
    def _validate_strategy_name(self) -> Self:
        """Ensure strategy is a known assignment strategy name.

        Returns:
            The validated model instance (``self``), unchanged.

        Raises:
            ValueError: When ``strategy`` is not one of the known
                assignment-strategy names.
        """
        if self.strategy not in self._VALID_STRATEGIES:
            msg = (
                f"Unknown assignment strategy {self.strategy!r}. "
                f"Valid strategies: "
                f"{sorted(self._VALID_STRATEGIES)}"
            )
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                model="TaskAssignmentConfig",
                error=msg,
                strategy=self.strategy,
            )
            raise ValueError(msg)
        return self
