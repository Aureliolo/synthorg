"""Facade-service accessors for ``AppState``.

Extracted from ``state_services.py`` to keep each module under the
project's 800-line ceiling.  Every accessor here is a thin pass-through
to a private slot attribute on the concrete :class:`AppState`; the
mixin is combined into :class:`AppStateServicesMixin` via inheritance.

Three groupings live in this mixin:

* Meta facades (signals / analytics / reports).
* Communication facades (messages / meetings / connections / webhooks /
  tunnel).
* META-MCP-2 facades (settings-read, providers-read, backup, users,
  projects, requests, setup, simulation, template packs, audit-read,
  events-read, integration health, company read, departments, teams,
  role versions, MCP catalog, OAuth, clients, artifacts, ontology,
  quality, reviews, evaluation versions).

Method-level docstrings are intentionally thin (``# ruff: noqa: D102``
on the class scope) because the property names are the API surface and
the class docstring describes the shared pattern.
"""

from typing import Any

from synthorg.api.rate_limits.config import PerOpRateLimitConfig  # noqa: TC001
from synthorg.api.rate_limits.inflight_config import (
    PerOpConcurrencyConfig,  # noqa: TC001
)
from synthorg.api.state_services_facades_mcp3 import _MetaMcp3FacadesMixin
from synthorg.api.state_services_facades_mcp4 import _MetaMcp4FacadesMixin
from synthorg.communication.meetings.service import MeetingService  # noqa: TC001
from synthorg.communication.messages.service import MessageService  # noqa: TC001
from synthorg.coordination.ceremony_policy.service import (
    CeremonyPolicyService,  # noqa: TC001
)
from synthorg.coordination.service import CoordinationService  # noqa: TC001
from synthorg.engine.quality.mcp_services import (
    EvaluationVersionService,  # noqa: TC001
    QualityFacadeService,  # noqa: TC001
    ReviewFacadeService,  # noqa: TC001
)
from synthorg.hr.activity_service import ActivityFeedService  # noqa: TC001
from synthorg.hr.health.service import AgentHealthService  # noqa: TC001
from synthorg.hr.identity.version_service import AgentVersionService  # noqa: TC001
from synthorg.hr.personalities.service import PersonalityService  # noqa: TC001
from synthorg.hr.scaling.decision_service import (
    ScalingDecisionService,  # noqa: TC001
)
from synthorg.infrastructure.services import (
    AuditReadService,  # noqa: TC001
    BackupFacadeService,  # noqa: TC001
    EventsReadService,  # noqa: TC001
    IntegrationHealthFacadeService,  # noqa: TC001
    ProjectFacadeService,  # noqa: TC001
    ProviderReadService,  # noqa: TC001
    RequestsFacadeService,  # noqa: TC001
    SettingsReadService,  # noqa: TC001
    SetupFacadeService,  # noqa: TC001
    SimulationFacadeService,  # noqa: TC001
    TemplatePackFacadeService,  # noqa: TC001
    UserFacadeService,  # noqa: TC001
)
from synthorg.integrations.connections.mcp_service import (
    ConnectionService,  # noqa: TC001
)
from synthorg.integrations.mcp_services import (
    ArtifactFacadeService,  # noqa: TC001
    ClientFacadeService,  # noqa: TC001
    MCPCatalogFacadeService,  # noqa: TC001
    OAuthFacadeService,  # noqa: TC001
    OntologyFacadeService,  # noqa: TC001
)
from synthorg.integrations.tunnel.mcp_service import TunnelService  # noqa: TC001
from synthorg.integrations.webhooks.service import WebhookService  # noqa: TC001
from synthorg.memory.service import MemoryService  # noqa: TC001
from synthorg.meta.analytics.service import AnalyticsService  # noqa: TC001
from synthorg.meta.reports.service import ReportsService  # noqa: TC001
from synthorg.meta.signals.service import SignalsService  # noqa: TC001
from synthorg.organization.services import (
    CompanyReadService,  # noqa: TC001
    DepartmentService,  # noqa: TC001
    RoleVersionService,  # noqa: TC001
    TeamService,  # noqa: TC001
)


class _FacadesMixin(_MetaMcp3FacadesMixin, _MetaMcp4FacadesMixin):
    """Mixin hosting all facade-service accessors for :class:`AppState`.

    Must be combined with the rest of ``AppStateServicesMixin`` via
    multiple inheritance so the shared helper methods
    (``_require_service``, ``_set_once``) resolve at runtime.

    The META-MCP-3 accessors (``workflow_service``,
    ``workflow_execution_service``, ``workflow_version_service``,
    ``subworkflow_service``, ``self_improvement_service``) live on
    :class:`_MetaMcp3FacadesMixin`.

    The META-MCP-4 accessors (``activity_feed_service``,
    ``agent_health_service``, ``agent_version_service``,
    ``ceremony_policy_service``, ``coordination_service``,
    ``memory_service``, ``personality_service``,
    ``scaling_decision_service``) live on
    :class:`_MetaMcp4FacadesMixin`.

    Both mixins exist as separate files purely to keep this module
    under the 800-line ceiling; they are composed in via inheritance.
    """

    _set_once: Any

    def _require_service[T](  # pragma: no cover
        self, service: T | None, name: str
    ) -> T:
        """Return *service* or raise (implemented on concrete ``AppState``)."""
        raise NotImplementedError

    def _init_facade_service_slots(self) -> None:
        """Initialise every facade-service slot attribute to ``None``.

        Concrete :class:`AppState` calls this once from ``__init__`` so
        the slots declared for facade services have stable starting
        values.  Extracted here to keep ``state.py`` under the project
        size limit.
        """
        self._signals_service = None
        self._analytics_service = None
        self._reports_service = None
        self._message_service = None
        self._meeting_service = None
        self._connection_service = None
        self._webhook_service = None
        self._tunnel_service = None
        self._settings_read_service = None
        self._provider_read_service = None
        self._backup_facade_service = None
        self._user_facade_service = None
        self._project_facade_service = None
        self._requests_facade_service = None
        self._setup_facade_service = None
        self._simulation_facade_service = None
        self._template_pack_facade_service = None
        self._audit_read_service = None
        self._events_read_service = None
        self._integration_health_facade_service = None
        self._company_read_service = None
        self._department_service = None
        self._team_service = None
        self._role_version_service = None
        self._mcp_catalog_facade_service = None
        self._oauth_facade_service = None
        self._client_facade_service = None
        self._artifact_facade_service = None
        self._ontology_facade_service = None
        self._quality_facade_service = None
        self._review_facade_service = None
        self._evaluation_version_service = None
        self._per_op_rate_limit_config = None
        self._per_op_concurrency_config = None
        # META-MCP-4 facades (observability + memory + coordination).
        self._activity_feed_service = None
        self._agent_health_service = None
        self._agent_version_service = None
        self._ceremony_policy_service = None
        self._coordination_service = None
        self._memory_service = None
        self._personality_service = None
        self._scaling_decision_service = None
        # META-MCP-3 facades (write-side services for workflows + meta).
        self._workflow_service = None
        self._workflow_execution_service = None
        self._workflow_version_service = None
        self._subworkflow_service = None
        self._self_improvement_service = None
        self._chief_of_staff_chat = None
        self._chief_of_staff_proposer = None
        self._charter_service = None
        self._charter_dispatcher = None
        self._conversational_proposal_repo = None

    # Slot attrs for facade services (populated on concrete AppState).
    _signals_service: SignalsService | None
    _analytics_service: AnalyticsService | None
    _reports_service: ReportsService | None
    _message_service: MessageService | None
    _meeting_service: MeetingService | None
    _connection_service: ConnectionService | None
    _webhook_service: WebhookService | None
    _tunnel_service: TunnelService | None
    _settings_read_service: SettingsReadService | None
    _provider_read_service: ProviderReadService | None
    _backup_facade_service: BackupFacadeService | None
    _user_facade_service: UserFacadeService | None
    _project_facade_service: ProjectFacadeService | None
    _requests_facade_service: RequestsFacadeService | None
    _setup_facade_service: SetupFacadeService | None
    _simulation_facade_service: SimulationFacadeService | None
    _template_pack_facade_service: TemplatePackFacadeService | None
    _audit_read_service: AuditReadService | None
    _events_read_service: EventsReadService | None
    _integration_health_facade_service: IntegrationHealthFacadeService | None
    _company_read_service: CompanyReadService | None
    _department_service: DepartmentService | None
    _team_service: TeamService | None
    _role_version_service: RoleVersionService | None
    _mcp_catalog_facade_service: MCPCatalogFacadeService | None
    _oauth_facade_service: OAuthFacadeService | None
    _client_facade_service: ClientFacadeService | None
    _artifact_facade_service: ArtifactFacadeService | None
    _ontology_facade_service: OntologyFacadeService | None
    _quality_facade_service: QualityFacadeService | None
    _review_facade_service: ReviewFacadeService | None
    _evaluation_version_service: EvaluationVersionService | None
    _per_op_rate_limit_config: PerOpRateLimitConfig | None
    _per_op_concurrency_config: PerOpConcurrencyConfig | None
    _activity_feed_service: ActivityFeedService | None
    _agent_health_service: AgentHealthService | None
    _agent_version_service: AgentVersionService | None
    _ceremony_policy_service: CeremonyPolicyService | None
    _coordination_service: CoordinationService | None
    _memory_service: MemoryService | None
    _personality_service: PersonalityService | None
    _scaling_decision_service: ScalingDecisionService | None

    # ── Signals / analytics / reports ─────────────────────────────

    @property
    def has_signals_service(self) -> bool:
        """Whether the signals facade is attached."""
        return self._signals_service is not None

    @property
    def signals_service(self) -> SignalsService:
        """The signals facade; raises ``ServiceUnavailableError`` when unset."""
        return self._require_service(self._signals_service, "SignalsService")

    def set_signals_service(self, service: SignalsService) -> None:
        """Attach the signals facade (once-only)."""
        self._set_once("_signals_service", service, "SignalsService")

    @property
    def has_analytics_service(self) -> bool:
        """Whether the analytics facade is attached."""
        return self._analytics_service is not None

    @property
    def analytics_service(self) -> AnalyticsService:
        """The analytics facade; raises ``ServiceUnavailableError`` when unset."""
        return self._require_service(self._analytics_service, "AnalyticsService")

    def set_analytics_service(self, service: AnalyticsService) -> None:
        """Attach the analytics facade (once-only)."""
        self._set_once("_analytics_service", service, "AnalyticsService")

    @property
    def has_reports_service(self) -> bool:
        """Whether the reports facade is attached."""
        return self._reports_service is not None

    @property
    def reports_service(self) -> ReportsService:
        """The reports facade; raises ``ServiceUnavailableError`` when unset."""
        return self._require_service(self._reports_service, "ReportsService")

    def set_reports_service(self, service: ReportsService) -> None:
        """Attach the reports facade (once-only)."""
        self._set_once("_reports_service", service, "ReportsService")

    # ── Communication facades ─────────────────────────────────────

    @property
    def has_message_service(self) -> bool:
        """Whether the message facade is attached."""
        return self._message_service is not None

    @property
    def message_service(self) -> MessageService:
        """The message facade; raises ``ServiceUnavailableError`` when unset."""
        return self._require_service(self._message_service, "MessageService")

    def set_message_service(self, service: MessageService) -> None:
        """Attach the message facade (once-only)."""
        self._set_once("_message_service", service, "MessageService")

    @property
    def has_meeting_service(self) -> bool:
        """Whether the meeting facade is attached."""
        return self._meeting_service is not None

    @property
    def meeting_service(self) -> MeetingService:
        """The meeting facade; raises ``ServiceUnavailableError`` when unset."""
        return self._require_service(self._meeting_service, "MeetingService")

    def set_meeting_service(self, service: MeetingService) -> None:
        """Attach the meeting facade (once-only)."""
        self._set_once("_meeting_service", service, "MeetingService")

    @property
    def has_connection_service(self) -> bool:
        """Whether the connection facade is attached."""
        return self._connection_service is not None

    @property
    def connection_service(self) -> ConnectionService:
        """The connection facade; raises ``ServiceUnavailableError`` when unset."""
        return self._require_service(self._connection_service, "ConnectionService")

    def set_connection_service(self, service: ConnectionService) -> None:
        """Attach the connection facade (once-only)."""
        self._set_once("_connection_service", service, "ConnectionService")

    @property
    def has_webhook_service(self) -> bool:
        """Whether the webhook facade is attached."""
        return self._webhook_service is not None

    @property
    def webhook_service(self) -> WebhookService:
        """The webhook facade; raises ``ServiceUnavailableError`` when unset."""
        return self._require_service(self._webhook_service, "WebhookService")

    def set_webhook_service(self, service: WebhookService) -> None:
        """Attach the webhook facade (once-only)."""
        self._set_once("_webhook_service", service, "WebhookService")

    @property
    def has_tunnel_service(self) -> bool:
        """Whether the tunnel facade is attached."""
        return self._tunnel_service is not None

    @property
    def tunnel_service(self) -> TunnelService:
        """The tunnel facade; raises ``ServiceUnavailableError`` when unset."""
        return self._require_service(self._tunnel_service, "TunnelService")

    def set_tunnel_service(self, service: TunnelService) -> None:
        """Attach the tunnel facade (once-only)."""
        self._set_once("_tunnel_service", service, "TunnelService")

    # ── Infrastructure facades ────────────────────────────────────
    # Trivial getter/setter pass-throughs; the class docstring covers
    # the pattern.

    @property
    def has_settings_read_service(self) -> bool:
        return self._settings_read_service is not None

    @property
    def settings_read_service(self) -> SettingsReadService:
        return self._require_service(
            self._settings_read_service,
            "SettingsReadService",
        )

    def set_settings_read_service(
        self,
        service: SettingsReadService,
    ) -> None:
        self._set_once("_settings_read_service", service, "SettingsReadService")

    @property
    def has_provider_read_service(self) -> bool:
        return self._provider_read_service is not None

    @property
    def provider_read_service(self) -> ProviderReadService:
        return self._require_service(
            self._provider_read_service,
            "ProviderReadService",
        )

    def set_provider_read_service(
        self,
        service: ProviderReadService,
    ) -> None:
        self._set_once("_provider_read_service", service, "ProviderReadService")

    @property
    def has_backup_facade_service(self) -> bool:
        return self._backup_facade_service is not None

    @property
    def backup_facade_service(self) -> BackupFacadeService:
        return self._require_service(
            self._backup_facade_service,
            "BackupFacadeService",
        )

    def set_backup_facade_service(
        self,
        service: BackupFacadeService,
    ) -> None:
        self._set_once("_backup_facade_service", service, "BackupFacadeService")

    @property
    def has_user_facade_service(self) -> bool:
        return self._user_facade_service is not None

    @property
    def user_facade_service(self) -> UserFacadeService:
        return self._require_service(self._user_facade_service, "UserFacadeService")

    def set_user_facade_service(self, service: UserFacadeService) -> None:
        self._set_once("_user_facade_service", service, "UserFacadeService")

    @property
    def has_project_facade_service(self) -> bool:
        return self._project_facade_service is not None

    @property
    def project_facade_service(self) -> ProjectFacadeService:
        return self._require_service(
            self._project_facade_service,
            "ProjectFacadeService",
        )

    def set_project_facade_service(
        self,
        service: ProjectFacadeService,
    ) -> None:
        self._set_once("_project_facade_service", service, "ProjectFacadeService")

    @property
    def has_requests_facade_service(self) -> bool:
        return self._requests_facade_service is not None

    @property
    def requests_facade_service(self) -> RequestsFacadeService:
        return self._require_service(
            self._requests_facade_service,
            "RequestsFacadeService",
        )

    def set_requests_facade_service(
        self,
        service: RequestsFacadeService,
    ) -> None:
        self._set_once("_requests_facade_service", service, "RequestsFacadeService")

    @property
    def has_setup_facade_service(self) -> bool:
        return self._setup_facade_service is not None

    @property
    def setup_facade_service(self) -> SetupFacadeService:
        return self._require_service(self._setup_facade_service, "SetupFacadeService")

    def set_setup_facade_service(
        self,
        service: SetupFacadeService,
    ) -> None:
        self._set_once("_setup_facade_service", service, "SetupFacadeService")

    @property
    def has_simulation_facade_service(self) -> bool:
        return self._simulation_facade_service is not None

    @property
    def simulation_facade_service(self) -> SimulationFacadeService:
        return self._require_service(
            self._simulation_facade_service,
            "SimulationFacadeService",
        )

    def set_simulation_facade_service(
        self,
        service: SimulationFacadeService,
    ) -> None:
        self._set_once(
            "_simulation_facade_service",
            service,
            "SimulationFacadeService",
        )

    @property
    def has_template_pack_facade_service(self) -> bool:
        return self._template_pack_facade_service is not None

    @property
    def template_pack_facade_service(self) -> TemplatePackFacadeService:
        return self._require_service(
            self._template_pack_facade_service,
            "TemplatePackFacadeService",
        )

    def set_template_pack_facade_service(
        self,
        service: TemplatePackFacadeService,
    ) -> None:
        self._set_once(
            "_template_pack_facade_service",
            service,
            "TemplatePackFacadeService",
        )

    @property
    def has_audit_read_service(self) -> bool:
        return self._audit_read_service is not None

    @property
    def audit_read_service(self) -> AuditReadService:
        return self._require_service(self._audit_read_service, "AuditReadService")

    def set_audit_read_service(self, service: AuditReadService) -> None:
        self._set_once("_audit_read_service", service, "AuditReadService")

    @property
    def has_events_read_service(self) -> bool:
        return self._events_read_service is not None

    @property
    def events_read_service(self) -> EventsReadService:
        return self._require_service(self._events_read_service, "EventsReadService")

    def set_events_read_service(self, service: EventsReadService) -> None:
        self._set_once("_events_read_service", service, "EventsReadService")

    @property
    def has_integration_health_facade_service(self) -> bool:
        return self._integration_health_facade_service is not None

    @property
    def integration_health_facade_service(
        self,
    ) -> IntegrationHealthFacadeService:
        return self._require_service(
            self._integration_health_facade_service,
            "IntegrationHealthFacadeService",
        )

    def set_integration_health_facade_service(
        self,
        service: IntegrationHealthFacadeService,
    ) -> None:
        self._set_once(
            "_integration_health_facade_service",
            service,
            "IntegrationHealthFacadeService",
        )

    # ── Organization facades ──────────────────────────────────────

    @property
    def has_company_read_service(self) -> bool:
        return self._company_read_service is not None

    @property
    def company_read_service(self) -> CompanyReadService:
        return self._require_service(
            self._company_read_service,
            "CompanyReadService",
        )

    def set_company_read_service(
        self,
        service: CompanyReadService,
    ) -> None:
        self._set_once("_company_read_service", service, "CompanyReadService")

    @property
    def has_department_service(self) -> bool:
        return self._department_service is not None

    @property
    def department_service(self) -> DepartmentService:
        return self._require_service(self._department_service, "DepartmentService")

    def set_department_service(self, service: DepartmentService) -> None:
        self._set_once("_department_service", service, "DepartmentService")

    @property
    def has_team_service(self) -> bool:
        return self._team_service is not None

    @property
    def team_service(self) -> TeamService:
        return self._require_service(self._team_service, "TeamService")

    def set_team_service(self, service: TeamService) -> None:
        self._set_once("_team_service", service, "TeamService")

    @property
    def has_role_version_service(self) -> bool:
        return self._role_version_service is not None

    @property
    def role_version_service(self) -> RoleVersionService:
        return self._require_service(
            self._role_version_service,
            "RoleVersionService",
        )

    def set_role_version_service(
        self,
        service: RoleVersionService,
    ) -> None:
        self._set_once("_role_version_service", service, "RoleVersionService")

    # ── Integration facades ───────────────────────────────────────

    @property
    def has_mcp_catalog_facade_service(self) -> bool:
        return self._mcp_catalog_facade_service is not None

    @property
    def mcp_catalog_facade_service(self) -> MCPCatalogFacadeService:
        return self._require_service(
            self._mcp_catalog_facade_service,
            "MCPCatalogFacadeService",
        )

    def set_mcp_catalog_facade_service(
        self,
        service: MCPCatalogFacadeService,
    ) -> None:
        self._set_once(
            "_mcp_catalog_facade_service",
            service,
            "MCPCatalogFacadeService",
        )

    @property
    def has_oauth_facade_service(self) -> bool:
        return self._oauth_facade_service is not None

    @property
    def oauth_facade_service(self) -> OAuthFacadeService:
        return self._require_service(
            self._oauth_facade_service,
            "OAuthFacadeService",
        )

    def set_oauth_facade_service(
        self,
        service: OAuthFacadeService,
    ) -> None:
        self._set_once("_oauth_facade_service", service, "OAuthFacadeService")

    @property
    def has_client_facade_service(self) -> bool:
        return self._client_facade_service is not None

    @property
    def client_facade_service(self) -> ClientFacadeService:
        return self._require_service(
            self._client_facade_service,
            "ClientFacadeService",
        )

    def set_client_facade_service(
        self,
        service: ClientFacadeService,
    ) -> None:
        self._set_once("_client_facade_service", service, "ClientFacadeService")

    @property
    def has_artifact_facade_service(self) -> bool:
        return self._artifact_facade_service is not None

    @property
    def artifact_facade_service(self) -> ArtifactFacadeService:
        return self._require_service(
            self._artifact_facade_service,
            "ArtifactFacadeService",
        )

    def set_artifact_facade_service(
        self,
        service: ArtifactFacadeService,
    ) -> None:
        self._set_once(
            "_artifact_facade_service",
            service,
            "ArtifactFacadeService",
        )

    @property
    def has_ontology_facade_service(self) -> bool:
        return self._ontology_facade_service is not None

    @property
    def ontology_facade_service(self) -> OntologyFacadeService:
        return self._require_service(
            self._ontology_facade_service,
            "OntologyFacadeService",
        )

    def set_ontology_facade_service(
        self,
        service: OntologyFacadeService,
    ) -> None:
        self._set_once(
            "_ontology_facade_service",
            service,
            "OntologyFacadeService",
        )

    # ── Quality facades ───────────────────────────────────────────

    @property
    def has_quality_facade_service(self) -> bool:
        return self._quality_facade_service is not None

    @property
    def quality_facade_service(self) -> QualityFacadeService:
        return self._require_service(
            self._quality_facade_service,
            "QualityFacadeService",
        )

    def set_quality_facade_service(
        self,
        service: QualityFacadeService,
    ) -> None:
        self._set_once(
            "_quality_facade_service",
            service,
            "QualityFacadeService",
        )

    @property
    def has_review_facade_service(self) -> bool:
        return self._review_facade_service is not None

    @property
    def review_facade_service(self) -> ReviewFacadeService:
        return self._require_service(
            self._review_facade_service,
            "ReviewFacadeService",
        )

    def set_review_facade_service(
        self,
        service: ReviewFacadeService,
    ) -> None:
        self._set_once(
            "_review_facade_service",
            service,
            "ReviewFacadeService",
        )

    @property
    def has_evaluation_version_service(self) -> bool:
        return self._evaluation_version_service is not None

    @property
    def evaluation_version_service(self) -> EvaluationVersionService:
        return self._require_service(
            self._evaluation_version_service,
            "EvaluationVersionService",
        )

    def set_evaluation_version_service(
        self,
        service: EvaluationVersionService,
    ) -> None:
        self._set_once(
            "_evaluation_version_service",
            service,
            "EvaluationVersionService",
        )

    # META-MCP-4 facades moved to ``state_services_facades_mcp4.py``
    # and composed into ``_FacadesMixin`` via inheritance so the
    # module stays under the 800-line ceiling.


__all__ = ["_FacadesMixin"]
