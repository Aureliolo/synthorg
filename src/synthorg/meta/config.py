"""Configuration for the self-improving company meta-loop.

Defines frozen Pydantic config models with safe defaults:
disabled by default, mandatory approval gate, conservative
thresholds.
"""

from typing import TYPE_CHECKING, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.core.critical_errors import reraise_critical
from synthorg.core.types import NotBlankStr
from synthorg.meta.charter.config import CharterConfig
from synthorg.meta.chief_of_staff.config import ChiefOfStaffConfig
from synthorg.meta.models import EvolutionMode, RolloutStrategyType
from synthorg.meta.telemetry.config import CrossDeploymentAnalyticsConfig
from synthorg.meta.toolsmith.config import ToolsmithConfig
from synthorg.observability import get_logger, safe_error_description
from synthorg.observability.events.meta import META_SELF_IMPROVEMENT_LOAD_FAILED

if TYPE_CHECKING:
    from synthorg.settings.service import SettingsService

logger = get_logger(__name__)


class RuleConfig(BaseModel):
    """Configuration for the signal rule engine.

    Attributes:
        disabled_rules: Names of built-in rules to disable.
        custom_rule_modules: Dotted module paths for user-defined rules.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    disabled_rules: tuple[NotBlankStr, ...] = ()
    custom_rule_modules: tuple[NotBlankStr, ...] = ()


class ABTestConfig(BaseModel):
    """Configuration for A/B test rollout behavior.

    Attributes:
        control_fraction: Fraction of agents in the control group.
        min_agents_per_group: Minimum agents required per group.
        min_observations_per_group: Minimum metric samples per group
            before statistical comparison is allowed.
        improvement_threshold: Minimum improvement ratio to
            declare treatment as winner.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    control_fraction: float = Field(default=0.5, gt=0.0, lt=1.0)
    min_agents_per_group: int = Field(default=5, ge=2)
    min_observations_per_group: int = Field(default=10, ge=2)
    improvement_threshold: float = Field(default=0.15, gt=0.0, le=1.0)


class RolloutConfig(BaseModel):
    """Configuration for proposal rollout behavior.

    Attributes:
        default_strategy: Default rollout strategy for proposals.
        observation_window_hours: Post-apply observation window.
        regression_check_interval_hours: How often to check for
            regression during the observation window.
        ab_test: A/B test-specific configuration.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    default_strategy: RolloutStrategyType = RolloutStrategyType.BEFORE_AFTER
    observation_window_hours: int = Field(default=48, ge=1)
    regression_check_interval_hours: int = Field(default=4, ge=1)
    ab_test: ABTestConfig = Field(default_factory=ABTestConfig)

    @model_validator(mode="after")
    def _validate_interval_within_window(self) -> Self:
        """Regression check interval must fit within observation window.

        Returns:
            ``Self`` instance.

        Raises:
            ValueError: Raised on the corresponding failure path.
        """
        if self.regression_check_interval_hours > self.observation_window_hours:
            msg = "regression_check_interval_hours must be <= observation_window_hours"
            raise ValueError(msg)
        return self


class RegressionConfig(BaseModel):
    """Configuration for regression detection thresholds.

    All values are fractional (0.10 = 10% degradation). Layer 1
    (threshold) fires instantly; layer 2 (statistical) fires after
    the observation window completes.

    Attributes:
        quality_drop_threshold: Max quality score drop (layer 1).
        cost_increase_threshold: Max cost increase (layer 1).
        error_rate_increase_threshold: Max error rate increase (layer 1).
        success_rate_drop_threshold: Max success rate drop (layer 1).
        statistical_significance_level: p-value for layer 2.
        min_data_points: Min data points for statistical test.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    quality_drop_threshold: float = Field(default=0.10, ge=0.0, le=1.0)
    cost_increase_threshold: float = Field(default=0.20, ge=0.0, le=1.0)
    error_rate_increase_threshold: float = Field(default=0.15, ge=0.0, le=1.0)
    success_rate_drop_threshold: float = Field(default=0.10, ge=0.0, le=1.0)
    statistical_significance_level: float = Field(default=0.05, ge=0.001, le=0.5)
    min_data_points: int = Field(default=10, ge=2)


class GuardChainConfig(BaseModel):
    """Configuration for the proposal guard chain.

    Attributes:
        proposal_rate_limit: Max proposals per rate window.
        rate_limit_window_hours: Duration of the rate limit window.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    proposal_rate_limit: int = Field(default=10, ge=1)
    rate_limit_window_hours: int = Field(default=24, ge=1)


class ScheduleConfig(BaseModel):
    """Configuration for improvement cycle scheduling.

    Attributes:
        cycle_interval_hours: Hours between scheduled cycles.
        inflection_trigger_enabled: Trigger on performance inflections.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    cycle_interval_hours: int = Field(default=168, ge=1)
    inflection_trigger_enabled: bool = True


class PromptTuningConfig(BaseModel):
    """Configuration for prompt tuning strategy behavior.

    Attributes:
        default_evolution_mode: Default interaction mode with
            the per-agent evolution system.
        allowed_modes: Which evolution modes are available.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    default_evolution_mode: EvolutionMode = EvolutionMode.ORG_WIDE
    allowed_modes: tuple[Literal["org_wide", "override", "advisory"], ...] = (
        "org_wide",
        "override",
        "advisory",
    )


class CodeModificationConfig(BaseModel):
    """Configuration for code modification strategy behavior.

    Controls which source paths the strategy may propose changes to,
    the LLM model used for code generation, and CI validation settings.

    Attributes:
        allowed_paths: Glob patterns for paths the strategy may modify.
        forbidden_paths: Glob patterns for paths the strategy must not
            touch (security, auth, compliance).
        llm_model: LLM model identifier for code generation.
        temperature: Sampling temperature (lower = more deterministic).
        max_tokens: Token budget for code generation responses.
        max_files_per_proposal: Maximum files changed per proposal.
        branch_prefix: Git branch prefix for generated branches.
        ci_timeout_seconds: Timeout for CI validation subprocess calls.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    allowed_paths: tuple[NotBlankStr, ...] = (
        NotBlankStr("src/synthorg/meta/strategies/*"),
        NotBlankStr("src/synthorg/meta/guards/*"),
        NotBlankStr("src/synthorg/meta/rules/*"),
        NotBlankStr("src/synthorg/meta/signals/*"),
    )
    forbidden_paths: tuple[NotBlankStr, ...] = (
        NotBlankStr("src/synthorg/core/security/*"),
        NotBlankStr("src/synthorg/auth/*"),
        NotBlankStr("src/synthorg/api/middleware/*"),
    )
    llm_model: NotBlankStr = Field(
        default=NotBlankStr("example-large-001"),
        description="Model for code generation LLM calls",
    )
    temperature: float = Field(default=0.2, ge=0.0, le=1.0)
    max_tokens: int = Field(default=8000, ge=100)
    max_files_per_proposal: int = Field(default=5, ge=1, le=20)
    branch_prefix: NotBlankStr = Field(
        default=NotBlankStr("meta/code-mod"),
    )
    base_branch: NotBlankStr = Field(
        default=NotBlankStr("main"),
        description="Default branch to create feature branches from",
    )
    project_root: NotBlankStr | None = Field(
        default=None,
        description=(
            "Absolute path to the project checkout. "
            "Defaults to the process working directory when None."
        ),
    )
    ci_timeout_seconds: int = Field(default=300, ge=30, le=600)
    api_timeout_seconds: int = Field(
        default=30,
        ge=5,
        le=120,
        description="Timeout for GitHub API HTTP requests",
    )
    github_token: NotBlankStr | None = Field(
        default=None,
        description=(
            "GitHub PAT or app installation token for API calls. "
            "Required when code_modification_enabled is True."
        ),
    )
    github_repo: NotBlankStr | None = Field(
        default=None,
        description=(
            "GitHub repository in owner/repo format. "
            "Required when code_modification_enabled is True."
        ),
    )
    github_api_url: NotBlankStr = Field(
        default=NotBlankStr("https://api.github.com"),
        description=(
            "GitHub API base URL.  Override for GitHub Enterprise"
            " installations; mirrors the"
            " ``integrations.github_api_url`` registry setting."
        ),
    )


class SelfImprovementConfig(BaseModel):
    """Top-level configuration for the self-improving company meta-loop.

    Safe defaults:
    - Feature: disabled (opt-in)
    - Chief of Staff agent: disabled (opt-in)
    - Altitudes: config_tuning ON when enabled; architecture + prompt + code OFF
    - Guards: all enabled, approval gate mandatory
    - Rollout: before/after default, 48h observation window
    - Regression: tiered (threshold + statistical)
    - Schedule: weekly + inflection triggers

    Attributes:
        enabled: Master switch for the self-improvement system.
        chief_of_staff_enabled: Whether to enable the Chief of Staff
            agent persona.
        config_tuning_enabled: Enable config tuning proposals.
        architecture_proposals_enabled: Enable architecture proposals.
        prompt_tuning_enabled: Enable prompt tuning proposals.
        code_modification_enabled: Enable code modification proposals.
        tool_creation_enabled: Enable self-extending toolkit proposals.
        schedule: Cycle scheduling configuration.
        rollout: Rollout behavior configuration.
        regression: Regression detection thresholds.
        guards: Guard chain configuration.
        rules: Rule engine configuration.
        prompt_tuning: Prompt tuning strategy configuration.
        code_modification: Code modification strategy configuration.
        chief_of_staff: Chief of Staff advanced capabilities
            (learning, alerts, chat).
        charter: Deep CEO interview to project charter capabilities.
        cross_deployment_analytics: Cross-deployment analytics
            telemetry (opt-in, disabled by default).
        toolsmith: Self-extending toolkit configuration
            (gap thresholds, sandbox policy, validation).
        analysis_model: LLM model identifier for proposal analysis.
        analysis_temperature: Sampling temperature for analysis.
        analysis_max_tokens: Token budget for analysis responses.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    enabled: bool = False
    chief_of_staff_enabled: bool = False

    config_tuning_enabled: bool = True
    architecture_proposals_enabled: bool = False
    prompt_tuning_enabled: bool = False
    code_modification_enabled: bool = False
    tool_creation_enabled: bool = False

    schedule: ScheduleConfig = Field(default_factory=ScheduleConfig)
    rollout: RolloutConfig = Field(default_factory=RolloutConfig)
    regression: RegressionConfig = Field(default_factory=RegressionConfig)
    guards: GuardChainConfig = Field(default_factory=GuardChainConfig)
    rules: RuleConfig = Field(default_factory=RuleConfig)
    prompt_tuning: PromptTuningConfig = Field(
        default_factory=PromptTuningConfig,
    )
    code_modification: CodeModificationConfig = Field(
        default_factory=CodeModificationConfig,
    )

    chief_of_staff: ChiefOfStaffConfig = Field(
        default_factory=ChiefOfStaffConfig,
    )

    charter: CharterConfig = Field(
        default_factory=CharterConfig,
    )

    cross_deployment_analytics: CrossDeploymentAnalyticsConfig = Field(
        default_factory=CrossDeploymentAnalyticsConfig,
    )

    toolsmith: ToolsmithConfig = Field(default_factory=ToolsmithConfig)

    analysis_model: NotBlankStr = Field(
        default=NotBlankStr("example-small-001"),
        description="Model for proposal analysis LLM calls",
    )
    analysis_temperature: float = Field(default=0.3, ge=0.0, le=2.0)
    analysis_max_tokens: int = Field(default=4000, ge=100)

    @model_validator(mode="after")
    def _validate_tool_creation_flags(self) -> Self:
        """Keep the two tool-creation switches coherent.

        ``tool_creation_enabled`` gates the boot wiring and scope guard,
        while ``toolsmith.enabled`` gates the toolsmith's own
        allowlist-consistency check. They must agree, otherwise an
        operator can enable one and silently leave the subsystem off (or
        configure an allowlist that never runs).

        Returns:
            ``Self`` instance.

        Raises:
            ValueError: Raised on the corresponding failure path.
        """
        if self.tool_creation_enabled != self.toolsmith.enabled:
            msg = "tool_creation_enabled and toolsmith.enabled must match"
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_code_modification_requirements(self) -> Self:
        """Require GitHub settings when code modification is enabled.

        Returns:
            ``Self`` instance.

        Raises:
            ValueError: Raised on the corresponding failure path.
        """
        if not self.code_modification_enabled:
            return self
        missing: list[str] = []
        if self.code_modification.github_token is None:
            missing.append("code_modification.github_token")
        if self.code_modification.github_repo is None:
            missing.append("code_modification.github_repo")
        if missing:
            msg = "code_modification_enabled requires: " + ", ".join(missing)
            raise ValueError(msg)
        return self


async def load_self_improvement_config(
    settings_service: SettingsService | None,
) -> SelfImprovementConfig:
    """Load ``SelfImprovementConfig`` from settings with safe-default fallback.

    Reads the ``meta.self_improvement`` JSON setting (an empty object by
    default) and merges it onto :class:`SelfImprovementConfig`'s code
    defaults.  Unknown keys, malformed JSON, or a missing settings
    service all return the pure default config so the controller
    never fails on read.

    Args:
        settings_service: The application's settings service, or None
            when unavailable (tests, degraded mode).

    Returns:
        A fully-constructed :class:`SelfImprovementConfig`.
    """
    if settings_service is None:
        return SelfImprovementConfig()
    try:
        entry = await settings_service.get("meta", "self_improvement")
    except Exception as exc:
        reraise_critical(exc)
        logger.warning(
            META_SELF_IMPROVEMENT_LOAD_FAILED,
            reason="settings_get_error",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return SelfImprovementConfig()
    raw = entry.value or "{}"
    try:
        import json as _json  # noqa: PLC0415 -- lazy stdlib import

        overrides = _json.loads(raw)
    except (ValueError, TypeError) as exc:
        logger.warning(
            META_SELF_IMPROVEMENT_LOAD_FAILED,
            reason="json_decode_error",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return SelfImprovementConfig()
    if not isinstance(overrides, dict) or not overrides:
        return SelfImprovementConfig()
    try:
        return SelfImprovementConfig(**overrides)
    except (ValueError, TypeError) as exc:
        logger.warning(
            META_SELF_IMPROVEMENT_LOAD_FAILED,
            reason="model_validation_failed",
            error_type=type(exc).__name__,
            error=safe_error_description(exc),
        )
        return SelfImprovementConfig()
