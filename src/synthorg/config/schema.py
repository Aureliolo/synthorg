# module-kind: code
"""Root configuration schema and config-level Pydantic models."""

from collections import Counter
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from synthorg.a2a.config import A2AConfig
from synthorg.api.config import ApiConfig
from synthorg.backup.config import BackupConfig
from synthorg.budget.config import BudgetConfig
from synthorg.budget.coordination_config import CoordinationMetricsConfig
from synthorg.budget.cost_tiers import CostTiersConfig
from synthorg.communication.config import CommunicationConfig
from synthorg.config.agent_schema import (
    AgentConfig,
    GracefulShutdownConfig,
    RoutingConfig,
    TaskAssignmentConfig,
)
from synthorg.config.model_metadata import ModelMetadata
from synthorg.config.posture_config import PostureConfig
from synthorg.config.provider_schema import (
    LocalModelParams,
    ProviderConfig,
    ProviderModelConfig,
)
from synthorg.core.company import CompanyConfig
from synthorg.core.company_departments import Department
from synthorg.core.company_handoffs import EscalationPath, WorkflowHandoff
from synthorg.core.role import CustomRole
from synthorg.core.types import NotBlankStr
from synthorg.engine.coordination.section_config import CoordinationSectionConfig
from synthorg.engine.evolution.config import EvolutionConfig
from synthorg.engine.routing_policy.config import StakesRoutingConfig
from synthorg.engine.stagnation.models import StagnationDetectionConfig
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
from synthorg.observability.audit_chain.config import AuditChainConfig
from synthorg.observability.config import LogConfig
from synthorg.observability.events.config import CONFIG_VALIDATION_FAILED
from synthorg.ontology.config import OntologyConfig
from synthorg.organization.enums import CompanyType
from synthorg.persistence.config import PersistenceConfig
from synthorg.security.config import SecurityConfig
from synthorg.security.trust.config import TrustConfig
from synthorg.telemetry.config import TelemetryConfig
from synthorg.tools.analytics.config import AnalyticsToolsConfig
from synthorg.tools.communication.config import CommunicationToolsConfig
from synthorg.tools.database.config import DatabaseConfig
from synthorg.tools.design.config import DesignToolsConfig
from synthorg.tools.disclosure_config import ToolDisclosureConfig
from synthorg.tools.git_url_validator import GitCloneNetworkPolicy
from synthorg.tools.mcp.config import MCPConfig
from synthorg.tools.sandbox.sandboxing_config import SandboxingConfig
from synthorg.tools.terminal.config import TerminalConfig
from synthorg.tools.web.config import WebToolsConfig
from synthorg.workers.config import QueueConfig

logger = get_logger(__name__)

__all__ = [
    "LocalModelParams",
    "ModelMetadata",
    "ProviderConfig",
    "ProviderModelConfig",
]


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
        stakes_routing: Stakes-aware model routing configuration (strategy
            discriminator, per-stakes quality floors, coordination nudge).
        logging: Logging configuration (``None`` to use platform defaults).
        audit_chain: Quantum-safe audit-chain sink configuration (opt-in,
            disabled by default).
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
        stagnation: Intra-loop stagnation detection selector and sub-configs.
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
        database: Database tool configuration (``None`` = no database tools).
        terminal: Terminal tool configuration (``None`` = default config).
        design_tools: Design tool configuration (``None`` = disabled).
        communication_tools: Communication tool configuration (``None`` = disabled).
        analytics_tools: Analytics tool configuration (``None`` = disabled).
        tool_disclosure: Progressive tool disclosure configuration.
        posture: Resolved operating-posture feature flags.
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
    stakes_routing: StakesRoutingConfig = Field(
        default_factory=StakesRoutingConfig,
        description="Stakes-aware model routing configuration",
    )
    logging: LogConfig | None = Field(
        default=None,
        description="Logging configuration",
    )
    audit_chain: AuditChainConfig = Field(
        default_factory=AuditChainConfig,
        description="Quantum-safe audit-chain sink configuration (opt-in)",
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
    evolution: EvolutionConfig = Field(default_factory=EvolutionConfig)
    stagnation: StagnationDetectionConfig = Field(
        default_factory=StagnationDetectionConfig,
        description="Intra-loop stagnation detection selector and sub-configs",
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
    posture: PostureConfig = Field(default_factory=PostureConfig)

    @model_validator(mode="after")
    def _validate_unique_agent_names(self) -> Self:
        """Ensure agent names are unique.

        Returns:
            The validated model instance (``self``), unchanged.

        Raises:
            ValueError: When two or more agents share a name.
        """
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
        """Ensure department names are unique.

        Returns:
            The validated model instance (``self``), unchanged.

        Raises:
            ValueError: When two or more departments share a name.
        """
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

        Returns:
            The validated model instance (``self``), unchanged.

        Raises:
            ValueError: When ``queue.enabled`` but the message-bus
                backend is not NATS, or the ``nats`` sub-block is unset.
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
        """Build unique model ref set, raising on cross-provider collisions.

        Returns:
            The set of every model id and alias across all providers.

        Raises:
            ValueError: When the same id or alias is defined by more than
                one provider.
        """
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
        """Ensure routing model references exist and are unambiguous.

        Returns:
            The validated model instance (``self``), unchanged.

        Raises:
            ValueError: When a routing rule, fallback, or fallback-chain
                entry references a model id/alias no provider defines.
        """
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
        """Ensure degradation fallback_providers reference known providers.

        Returns:
            The validated model instance (``self``), unchanged.

        Raises:
            ValueError: When a provider's degradation ``fallback_providers``
                names a provider absent from the config.
        """
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
