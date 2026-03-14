"""Root configuration schema and config-level Pydantic models."""

from collections import Counter
from typing import Any, ClassVar, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.api.config import ApiConfig
from synthorg.budget.config import BudgetConfig
from synthorg.budget.coordination_config import CoordinationMetricsConfig
from synthorg.budget.cost_tiers import CostTiersConfig
from synthorg.budget.quota import DegradationConfig, SubscriptionConfig
from synthorg.communication.config import CommunicationConfig
from synthorg.core.company import (
    CompanyConfig,
    Department,
    EscalationPath,
    WorkflowHandoff,
)
from synthorg.core.enums import AutonomyLevel, CompanyType, SeniorityLevel
from synthorg.core.resilience_config import RateLimiterConfig, RetryConfig
from synthorg.core.role import CustomRole  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.coordination.section_config import CoordinationSectionConfig
from synthorg.engine.task_engine_config import TaskEngineConfig
from synthorg.hr.promotion.config import PromotionConfig
from synthorg.memory.config import CompanyMemoryConfig
from synthorg.memory.org.config import OrgMemoryConfig
from synthorg.observability import get_logger
from synthorg.observability.config import LogConfig  # noqa: TC001
from synthorg.observability.events.config import CONFIG_VALIDATION_FAILED
from synthorg.persistence.config import PersistenceConfig
from synthorg.security.config import SecurityConfig
from synthorg.security.trust.config import TrustConfig
from synthorg.tools.mcp.config import MCPConfig
from synthorg.tools.sandbox.sandboxing_config import SandboxingConfig

logger = get_logger(__name__)


class ProviderModelConfig(BaseModel):
    """Configuration for a single LLM model within a provider.

    Attributes:
        id: Model identifier (e.g. ``"example-medium-001"``).
        alias: Short alias for referencing this model in routing rules.
        cost_per_1k_input: Cost per 1 000 input tokens in USD.
        cost_per_1k_output: Cost per 1 000 output tokens in USD.
        max_context: Maximum context window size in tokens.
        estimated_latency_ms: Estimated median latency in milliseconds.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    id: NotBlankStr = Field(description="Model identifier")
    alias: NotBlankStr | None = Field(
        default=None,
        description="Short alias for routing rules",
    )
    cost_per_1k_input: float = Field(
        default=0.0,
        ge=0.0,
        description="Cost per 1k input tokens in USD",
    )
    cost_per_1k_output: float = Field(
        default=0.0,
        ge=0.0,
        description="Cost per 1k output tokens in USD",
    )
    max_context: int = Field(
        default=200_000,
        gt=0,
        description="Maximum context window size in tokens",
    )
    estimated_latency_ms: int | None = Field(
        default=None,
        gt=0,
        le=300_000,
        description="Estimated median latency in milliseconds",
    )


class ProviderConfig(BaseModel):
    """Configuration for an LLM provider.

    Attributes:
        driver: Driver backend name (e.g. ``"litellm"``).
        api_key: API key (typically injected by secret management).
        base_url: Base URL for the provider API.
        models: Available models for this provider.
        retry: Retry configuration for transient errors.
        rate_limiter: Client-side rate limiting configuration.
        subscription: Subscription and quota configuration.
        degradation: Degradation strategy when quota exhausted.
    """

    model_config = ConfigDict(frozen=True)

    driver: NotBlankStr = Field(
        default="litellm",
        description="Driver backend name",
    )
    api_key: NotBlankStr | None = Field(
        default=None,
        repr=False,
        description="API key",
    )
    base_url: NotBlankStr | None = Field(
        default=None,
        description="Base URL for the provider API",
    )
    models: tuple[ProviderModelConfig, ...] = Field(
        default=(),
        description="Available models",
    )
    retry: RetryConfig = Field(
        default_factory=RetryConfig,
        description="Retry configuration for transient errors",
    )
    rate_limiter: RateLimiterConfig = Field(
        default_factory=RateLimiterConfig,
        description="Client-side rate limiting configuration",
    )
    subscription: SubscriptionConfig = Field(
        default_factory=SubscriptionConfig,
        description="Subscription and quota configuration",
    )
    degradation: DegradationConfig = Field(
        default_factory=DegradationConfig,
        description="Degradation strategy when quota exhausted",
    )

    @model_validator(mode="after")
    def _validate_unique_model_identifiers(self) -> Self:
        """Ensure model IDs and aliases are each unique."""
        ids = [m.id for m in self.models]
        if len(ids) != len(set(ids)):
            dupes = sorted(i for i, c in Counter(ids).items() if c > 1)
            msg = f"Duplicate model IDs: {dupes}"
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                model="ProviderConfig",
                error=msg,
            )
            raise ValueError(msg)
        aliases = [m.alias for m in self.models if m.alias is not None]
        if len(aliases) != len(set(aliases)):
            dupes = sorted(a for a, c in Counter(aliases).items() if c > 1)
            msg = f"Duplicate model aliases: {dupes}"
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                model="ProviderConfig",
                error=msg,
            )
            raise ValueError(msg)
        return self


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

    model_config = ConfigDict(frozen=True)

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

    model_config = ConfigDict(frozen=True)

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


class AgentConfig(BaseModel):
    """Agent configuration from YAML.

    Uses raw dicts for personality, model, memory, tools, and authority
    because :class:`~synthorg.core.agent.AgentIdentity` has runtime
    fields (``id``, ``hiring_date``, ``status``) that are not present in
    config.  The engine constructs full ``AgentIdentity`` objects at
    startup.

    Attributes:
        name: Agent display name.
        role: Role name.
        department: Department name.
        level: Seniority level.
        personality: Raw personality config dict.
        model: Raw model config dict.
        memory: Raw memory config dict.
        tools: Raw tools config dict.
        authority: Raw authority config dict.
    """

    model_config = ConfigDict(frozen=True)

    name: NotBlankStr = Field(description="Agent display name")
    role: NotBlankStr = Field(description="Role name")
    department: NotBlankStr = Field(description="Department name")
    level: SeniorityLevel = Field(
        default=SeniorityLevel.MID,
        description="Seniority level",
    )
    personality: dict[str, Any] = Field(
        default_factory=dict,
        description="Raw personality config",
    )
    model: dict[str, Any] = Field(
        default_factory=dict,
        description="Raw model config",
    )
    memory: dict[str, Any] = Field(
        default_factory=dict,
        description="Raw memory config",
    )
    tools: dict[str, Any] = Field(
        default_factory=dict,
        description="Raw tools config",
    )
    authority: dict[str, Any] = Field(
        default_factory=dict,
        description="Raw authority config",
    )
    autonomy_level: AutonomyLevel | None = Field(
        default=None,
        description="Per-agent autonomy level override (D6)",
    )


class GracefulShutdownConfig(BaseModel):
    """Configuration for graceful shutdown behaviour.

    Attributes:
        strategy: Shutdown strategy name (e.g. ``"cooperative_timeout"``).
        grace_seconds: Seconds to wait for cooperative agent exit
            before force-cancelling.
        cleanup_seconds: Seconds allowed for cleanup callbacks
            (persist costs, close connections, flush logs).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    strategy: NotBlankStr = Field(
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


class TaskAssignmentConfig(BaseModel):
    """Configuration for task assignment behaviour.

    Attributes:
        strategy: Assignment strategy name (e.g. ``"role_based"``).
        min_score: Minimum capability score for agent eligibility.
        max_concurrent_tasks_per_agent: Maximum tasks an agent can
            handle concurrently. Enforced by scoring-based strategies
            that filter out agents at capacity.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False)

    # Known strategy names — must stay in sync with
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
        """Ensure strategy is a known assignment strategy name."""
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


class RootConfig(BaseModel):
    """Root company configuration — the top-level validation target.

    Aggregates all sub-configurations into a single frozen model that
    represents a fully validated company setup.

    Attributes:
        company_name: Company name (required).
        company_type: Company template type.
        departments: Organizational departments.
        agents: Agent configurations.
        custom_roles: User-defined custom roles.
        config: Company-wide settings.
        budget: Budget configuration.
        communication: Communication configuration.
        providers: LLM provider configurations keyed by provider name.
        routing: Model routing configuration.
        logging: Logging configuration (``None`` to use platform defaults).
        graceful_shutdown: Graceful shutdown configuration.
        workflow_handoffs: Cross-department workflow handoffs.
        escalation_paths: Cross-department escalation paths.
        coordination_metrics: Coordination metrics configuration.
        task_assignment: Task assignment configuration.
        memory: Memory backend configuration.
        persistence: Persistence backend configuration.
        cost_tiers: Cost tier definitions.
        org_memory: Organizational memory configuration.
        api: API server configuration.
        sandboxing: Sandboxing backend configuration.
        mcp: MCP bridge configuration.
        security: Security subsystem configuration.
        trust: Progressive trust configuration.
        promotion: Promotion/demotion configuration.
        task_engine: Task engine configuration.
        coordination: Multi-agent coordination configuration.
    """

    model_config = ConfigDict(frozen=True)

    company_name: NotBlankStr = Field(
        description="Company name",
    )
    company_type: CompanyType = Field(
        default=CompanyType.CUSTOM,
        description="Company template type",
    )
    departments: tuple[Department, ...] = Field(
        default=(),
        description="Organizational departments",
    )
    agents: tuple[AgentConfig, ...] = Field(
        default=(),
        description="Agent configurations",
    )
    custom_roles: tuple[CustomRole, ...] = Field(
        default=(),
        description="User-defined custom roles",
    )
    config: CompanyConfig = Field(
        default_factory=CompanyConfig,
        description="Company-wide settings",
    )
    budget: BudgetConfig = Field(
        default_factory=BudgetConfig,
        description="Budget configuration",
    )
    communication: CommunicationConfig = Field(
        default_factory=CommunicationConfig,
        description="Communication configuration",
    )
    providers: dict[str, ProviderConfig] = Field(
        default_factory=dict,
        description="LLM provider configurations",
    )
    routing: RoutingConfig = Field(
        default_factory=RoutingConfig,
        description="Model routing configuration",
    )
    logging: LogConfig | None = Field(
        default=None,
        description="Logging configuration",
    )
    graceful_shutdown: GracefulShutdownConfig = Field(
        default_factory=GracefulShutdownConfig,
        description="Graceful shutdown configuration",
    )
    workflow_handoffs: tuple[WorkflowHandoff, ...] = Field(
        default=(),
        description="Cross-department workflow handoffs",
    )
    escalation_paths: tuple[EscalationPath, ...] = Field(
        default=(),
        description="Cross-department escalation paths",
    )
    coordination_metrics: CoordinationMetricsConfig = Field(
        default_factory=CoordinationMetricsConfig,
        description="Coordination metrics configuration",
    )
    task_assignment: TaskAssignmentConfig = Field(
        default_factory=TaskAssignmentConfig,
        description="Task assignment configuration",
    )
    memory: CompanyMemoryConfig = Field(
        default_factory=CompanyMemoryConfig,
        description="Memory backend configuration",
    )
    persistence: PersistenceConfig = Field(
        default_factory=PersistenceConfig,
        description="Persistence backend configuration",
    )
    cost_tiers: CostTiersConfig = Field(
        default_factory=CostTiersConfig,
        description="Cost tier definitions",
    )
    org_memory: OrgMemoryConfig = Field(
        default_factory=OrgMemoryConfig,
        description="Organizational memory configuration",
    )
    api: ApiConfig = Field(
        default_factory=ApiConfig,
        description="API server configuration",
    )
    sandboxing: SandboxingConfig = Field(
        default_factory=SandboxingConfig,
        description="Sandboxing backend configuration",
    )
    mcp: MCPConfig = Field(
        default_factory=MCPConfig,
        description="MCP bridge configuration",
    )
    security: SecurityConfig = Field(
        default_factory=SecurityConfig,
        description="Security subsystem configuration",
    )
    trust: TrustConfig = Field(
        default_factory=TrustConfig,
        description="Progressive trust configuration",
    )
    promotion: PromotionConfig = Field(
        default_factory=PromotionConfig,
        description="Promotion/demotion configuration",
    )
    task_engine: TaskEngineConfig = Field(
        default_factory=TaskEngineConfig,
        description="Task engine configuration",
    )
    coordination: CoordinationSectionConfig = Field(
        default_factory=CoordinationSectionConfig,
        description="Multi-agent coordination configuration",
    )

    @model_validator(mode="after")
    def _validate_unique_agent_names(self) -> Self:
        """Ensure agent names are unique."""
        names = [a.name for a in self.agents]
        if len(names) != len(set(names)):
            dupes = sorted(n for n, c in Counter(names).items() if c > 1)
            msg = f"Duplicate agent names: {dupes}"
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                model="RootConfig",
                error=msg,
            )
            raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_unique_department_names(self) -> Self:
        """Ensure department names are unique."""
        names = [d.name for d in self.departments]
        if len(names) != len(set(names)):
            dupes = sorted(n for n, c in Counter(names).items() if c > 1)
            msg = f"Duplicate department names: {dupes}"
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                model="RootConfig",
                error=msg,
            )
            raise ValueError(msg)
        return self

    def _collect_model_refs(self) -> set[str]:
        """Build unique model ref set, raising on cross-provider collisions."""
        ref_to_provider: dict[str, str] = {}
        for prov_name, provider in self.providers.items():
            for model in provider.models:
                for ref in (model.id, model.alias):
                    if ref is None:
                        continue
                    if ref in ref_to_provider:
                        msg = (
                            f"Ambiguous model reference {ref!r}: "
                            f"defined in both {ref_to_provider[ref]!r} "
                            f"and {prov_name!r}"
                        )
                        logger.warning(
                            CONFIG_VALIDATION_FAILED,
                            model="RootConfig",
                            error=msg,
                        )
                        raise ValueError(msg)
                    ref_to_provider[ref] = prov_name
        return set(ref_to_provider)

    @model_validator(mode="after")
    def _validate_routing_references(self) -> Self:
        """Ensure routing model references exist and are unambiguous."""
        if not self.routing.rules and not self.routing.fallback_chain:
            return self

        known_models = self._collect_model_refs()

        for rule in self.routing.rules:
            if rule.preferred_model not in known_models:
                msg = f"Routing rule references unknown model: {rule.preferred_model!r}"
                logger.warning(
                    CONFIG_VALIDATION_FAILED,
                    model="RootConfig",
                    error=msg,
                )
                raise ValueError(msg)
            if rule.fallback and rule.fallback not in known_models:
                msg = f"Routing rule references unknown fallback: {rule.fallback!r}"
                logger.warning(
                    CONFIG_VALIDATION_FAILED,
                    model="RootConfig",
                    error=msg,
                )
                raise ValueError(msg)

        for model_ref in self.routing.fallback_chain:
            if model_ref not in known_models:
                msg = f"Routing fallback_chain references unknown model: {model_ref!r}"
                logger.warning(
                    CONFIG_VALIDATION_FAILED,
                    model="RootConfig",
                    error=msg,
                )
                raise ValueError(msg)
        return self

    @model_validator(mode="after")
    def _validate_degradation_fallback_providers(self) -> Self:
        """Ensure degradation fallback_providers reference known providers."""
        known_providers = set(self.providers)
        for prov_name, prov_config in self.providers.items():
            for fb in prov_config.degradation.fallback_providers:
                if fb not in known_providers:
                    msg = (
                        f"Provider {prov_name!r} degradation "
                        f"fallback_providers references unknown "
                        f"provider: {fb!r}"
                    )
                    logger.warning(
                        CONFIG_VALIDATION_FAILED,
                        model="RootConfig",
                        error=msg,
                    )
                    raise ValueError(msg)
        return self
