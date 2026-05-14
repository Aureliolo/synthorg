"""Root configuration schema and config-level Pydantic models."""

from collections import Counter
from typing import Any, ClassVar, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.a2a.config import A2AConfig
from synthorg.api.config import ApiConfig
from synthorg.backup.config import BackupConfig
from synthorg.budget.config import BudgetConfig
from synthorg.budget.coordination_config import CoordinationMetricsConfig
from synthorg.budget.cost_tiers import CostTiersConfig
from synthorg.communication.config import CommunicationConfig
from synthorg.core.company import (
    CompanyConfig,
    Department,
    EscalationPath,
    WorkflowHandoff,
)
from synthorg.core.enums import (
    AutonomyLevel,
    CompanyType,
    SeniorityLevel,
    StrategicOutputMode,
)
from synthorg.core.role import CustomRole  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001
from synthorg.engine.coordination.section_config import CoordinationSectionConfig
from synthorg.engine.strategy.models import StrategyConfig
from synthorg.engine.task_engine_config import TaskEngineConfig
from synthorg.engine.workflow.config import WorkflowConfig
from synthorg.hr.performance.config import PerformanceConfig
from synthorg.hr.promotion.config import PromotionConfig
from synthorg.hr.training.config import TrainingConfig
from synthorg.integrations.config import IntegrationsConfig
from synthorg.memory.config import CompanyMemoryConfig
from synthorg.memory.org.config import OrgMemoryConfig
from synthorg.notifications.config import NotificationConfig
from synthorg.observability import get_logger
from synthorg.observability.config import LogConfig  # noqa: TC001
from synthorg.observability.events.config import (
    CONFIG_VALIDATION_FAILED,
)
from synthorg.ontology.config import OntologyConfig
from synthorg.persistence.config import PersistenceConfig
from synthorg.security.config import SecurityConfig
from synthorg.security.trust.config import TrustConfig
from synthorg.settings.enums import SettingNamespace
from synthorg.settings.mirrors import MirrorField, apply_settings_mirrors
from synthorg.telemetry.config import TelemetryConfig
from synthorg.tools.analytics.config import AnalyticsToolsConfig  # noqa: TC001
from synthorg.tools.communication.config import CommunicationToolsConfig  # noqa: TC001
from synthorg.tools.database.config import DatabaseConfig  # noqa: TC001
from synthorg.tools.design.config import DesignToolsConfig  # noqa: TC001
from synthorg.tools.disclosure_config import ToolDisclosureConfig
from synthorg.tools.git_url_validator import GitCloneNetworkPolicy
from synthorg.tools.mcp.config import MCPConfig
from synthorg.tools.sandbox.sandboxing_config import SandboxingConfig
from synthorg.tools.terminal.config import TerminalConfig  # noqa: TC001
from synthorg.tools.web.config import WebToolsConfig  # noqa: TC001
from synthorg.workers.config import QueueConfig

logger = get_logger(__name__)


from synthorg.config.provider_schema import (  # noqa: E402
    LocalModelParams,
    ProviderConfig,
    ProviderModelConfig,
)

__all__ = [
    "LocalModelParams",
    "ProviderConfig",
    "ProviderModelConfig",
]


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
    def _apply_mirrors(cls, data: Any) -> Any:
        return apply_settings_mirrors(data, cls._MIRROR_FIELDS)


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
        autonomy_level: Per-agent autonomy level override
            (``None`` inherits default).
        strategic_output_mode: Per-agent strategic output mode override
            (``StrategicOutputMode | None``).  ``None`` inherits the
            company strategy config default.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    name: NotBlankStr = Field(description="Agent display name")
    role: NotBlankStr = Field(description="Role name")
    department: NotBlankStr = Field(description="Department name")
    level: SeniorityLevel = Field(
        default=SeniorityLevel.MID,
        description="Seniority level",
    )
    personality_preset: NotBlankStr | None = Field(
        default=None,
        description=(
            "Named personality preset.  ``setup_agents`` writes the "
            "resolved preset name back when bootstrapping from a "
            "template, so the company-agents setting must round-trip "
            "the field rather than reject it under ``extra=forbid``."
        ),
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
    strategic_output_mode: StrategicOutputMode | None = Field(
        default=None,
        description=(
            "Per-agent strategic output mode override. "
            "None inherits the company strategy config default."
        ),
    )
    tier: Literal["large", "medium", "small"] | None = Field(
        default=None,
        description=(
            "Resolved model tier from the setup wizard "
            "(``large`` / ``medium`` / ``small``). Round-trips so the "
            "company-agents setting survives Pydantic validation when "
            "the wizard persists tier alongside the model selection."
        ),
    )
    model_requirement: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Structured model requirement dict emitted by the setup "
            "wizard (tier / priority / min_context / capabilities). "
            "Stored as a raw dict because the canonical "
            "``ModelRequirement`` model lives in ``synthorg.templates`` "
            "and importing it here would create a config -> templates "
            "cycle; the matcher rehydrates the dict at use sites."
        ),
    )


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
    """Root company configuration -- the top-level validation target.

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
        performance: Performance tracking configuration (quality judge,
            CI/LLM weights, trend thresholds).
        training: Training pipeline configuration.
        task_engine: Task engine configuration.
        queue: Distributed task queue configuration (opt-in, requires
            a distributed bus backend such as NATS).
        coordination: Multi-agent coordination configuration.
        strategy: Strategy and trendslop mitigation configuration.
        git_clone: Git clone SSRF prevention network policy.
        backup: Backup and restore configuration.
        workflow: Workflow type configuration.
        notifications: Notification subsystem configuration.
        integrations: External service integrations configuration.
        a2a: A2A external gateway configuration (disabled by default).
        ontology: Semantic ontology configuration.
        telemetry: Anonymous product telemetry configuration (opt-in,
            disabled by default).
        web: Web tool configuration (``None`` = default web config).
        database: Database tool configuration (``None`` = no database
            tools).
        terminal: Terminal tool configuration (``None`` = default
            terminal config).
        design_tools: Design tool configuration (``None`` = disabled).
        communication_tools: Communication tool configuration
            (``None`` = disabled).
        analytics_tools: Analytics tool configuration
            (``None`` = disabled).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

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
    performance: PerformanceConfig = Field(
        default_factory=PerformanceConfig,
        description="Performance tracking configuration",
    )
    training: TrainingConfig = Field(
        default_factory=TrainingConfig,
        description="Training pipeline configuration",
    )
    task_engine: TaskEngineConfig = Field(
        default_factory=TaskEngineConfig,
        description="Task engine configuration",
    )
    queue: QueueConfig = Field(
        default_factory=QueueConfig,
        description="Distributed task queue configuration (opt-in)",
    )
    coordination: CoordinationSectionConfig = Field(
        default_factory=CoordinationSectionConfig,
        description="Multi-agent coordination configuration",
    )
    strategy: StrategyConfig = Field(
        default_factory=StrategyConfig,
        description="Strategy and trendslop mitigation configuration",
    )
    git_clone: GitCloneNetworkPolicy = Field(
        default_factory=GitCloneNetworkPolicy,
        description="Git clone SSRF prevention network policy",
    )
    backup: BackupConfig = Field(
        default_factory=BackupConfig,
        description="Backup and restore configuration",
    )
    workflow: WorkflowConfig = Field(
        default_factory=WorkflowConfig,
        description="Workflow type configuration",
    )
    notifications: NotificationConfig = Field(
        default_factory=NotificationConfig,
        description="Notification subsystem configuration",
    )
    integrations: IntegrationsConfig = Field(
        default_factory=IntegrationsConfig,
        description="External service integrations configuration",
    )
    a2a: A2AConfig = Field(
        default_factory=A2AConfig,
        description="A2A external gateway configuration (disabled by default)",
    )
    ontology: OntologyConfig = Field(
        default_factory=OntologyConfig,
        description="Semantic ontology configuration",
    )
    telemetry: TelemetryConfig = Field(
        default_factory=TelemetryConfig,
        description="Anonymous product telemetry configuration (opt-in)",
    )
    web: WebToolsConfig | None = Field(
        default=None,
        description="Web tool configuration (None = default web config)",
    )
    database: DatabaseConfig | None = Field(
        default=None,
        description="Database tool configuration (None = no database tools)",
    )
    terminal: TerminalConfig | None = Field(
        default=None,
        description="Terminal tool configuration (None = default terminal config)",
    )
    design_tools: DesignToolsConfig | None = Field(
        default=None,
        description="Design tool configuration (None = disabled)",
    )
    communication_tools: CommunicationToolsConfig | None = Field(
        default=None,
        description="Communication tool configuration (None = disabled)",
    )
    analytics_tools: AnalyticsToolsConfig | None = Field(
        default=None,
        description="Analytics tool configuration (None = disabled)",
    )
    tool_disclosure: ToolDisclosureConfig = Field(
        default_factory=ToolDisclosureConfig,
        description="Progressive tool disclosure configuration",
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

    @model_validator(mode="after")
    def _validate_queue_requires_distributed_bus(self) -> Self:
        """Ensure ``queue.enabled`` requires an implemented distributed backend.

        The distributed task queue currently publishes claims through
        the JetStream work-queue client. Require ``backend == NATS``
        explicitly so config load fails fast when the selected
        transport cannot drive the queue, and additionally require a
        non-null ``nats`` sub-block so the worker has something to
        connect to.
        """
        from synthorg.communication.enums import MessageBusBackend  # noqa: PLC0415

        if not self.queue.enabled:
            return self
        backend = self.communication.message_bus.backend
        if backend != MessageBusBackend.NATS:
            msg = (
                "queue.enabled requires communication.message_bus.backend=='nats'; "
                f"got {backend.value!r}. Only NATS has a shipped task-queue client."
            )
            logger.warning(
                CONFIG_VALIDATION_FAILED,
                model="RootConfig",
                error=msg,
            )
            raise ValueError(msg)
        if self.communication.message_bus.nats is None:
            msg = (
                "queue.enabled requires communication.message_bus.nats to be set "
                "so the worker has a server URL and credentials to connect to."
            )
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
