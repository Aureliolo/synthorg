"""API controllers for all resource groups."""

from collections.abc import Callable
from typing import TYPE_CHECKING

from litestar import Controller

from synthorg.api.auth.controllers.api_keys import AuthApiKeysController
from synthorg.api.auth.controllers.bootstrap import AuthBootstrapController
from synthorg.api.auth.controllers.credentials import AuthCredentialsController
from synthorg.api.auth.controllers.identity import AuthIdentityController
from synthorg.api.auth.controllers.session import AuthSessionController
from synthorg.api.auth.controllers.sessions_mgmt import AuthSessionsController
from synthorg.api.controllers.activities.feed import ActivityController
from synthorg.api.controllers.agent_identity_versions import (
    AgentIdentityVersionController,
)
from synthorg.api.controllers.agent_roster import AgentRosterController
from synthorg.api.controllers.agents.crud import AgentCrudController
from synthorg.api.controllers.agents.observability import AgentObservabilityController
from synthorg.api.controllers.analytics.forecast import AnalyticsForecastController
from synthorg.api.controllers.analytics.overview import AnalyticsOverviewController
from synthorg.api.controllers.analytics.trends import AnalyticsTrendsController
from synthorg.api.controllers.approvals.decisions import ApprovalsDecisionsController
from synthorg.api.controllers.approvals.query import ApprovalsQueryController
from synthorg.api.controllers.artifacts import ArtifactController
from synthorg.api.controllers.audit import AuditController
from synthorg.api.controllers.autonomy import AutonomyController
from synthorg.api.controllers.backup import BackupController
from synthorg.api.controllers.brownfield import BrownfieldController
from synthorg.api.controllers.budget import BudgetController
from synthorg.api.controllers.budget_config_versions import (
    BudgetConfigVersionController,
)
from synthorg.api.controllers.budget_forecast import ForecastBudgetController
from synthorg.api.controllers.capabilities import CapabilitiesController
from synthorg.api.controllers.ceremony_policy import (
    CeremonyPolicyController,
)
from synthorg.api.controllers.charter import CharterController
from synthorg.api.controllers.clients import ClientController
from synthorg.api.controllers.cockpit import CockpitController
from synthorg.api.controllers.collaboration import CollaborationController
from synthorg.api.controllers.company import CompanyController
from synthorg.api.controllers.company_versions import (
    CompanyVersionController,
)
from synthorg.api.controllers.connections import ConnectionsController
from synthorg.api.controllers.conversational import ConversationalController
from synthorg.api.controllers.coordination import CoordinationController
from synthorg.api.controllers.coordination_metrics import (
    CoordinationMetricsController,
)
from synthorg.api.controllers.custom_rules import CustomRuleController
from synthorg.api.controllers.departments.ceremony_policy import (
    DepartmentCeremonyPolicyController,
)
from synthorg.api.controllers.departments.crud import DepartmentController
from synthorg.api.controllers.departments.health import DepartmentHealthController
from synthorg.api.controllers.escalations import EscalationsController
from synthorg.api.controllers.evaluation_config_versions import (
    EvaluationConfigVersionController,
)
from synthorg.api.controllers.events.interrupts import InterruptController
from synthorg.api.controllers.events.stream import EventStreamController
from synthorg.api.controllers.experiments import ExperimentsController
from synthorg.api.controllers.health import (
    HealthController,
    LivenessController,
    ReadinessController,
)
from synthorg.api.controllers.integration_health import (
    IntegrationHealthController,
)
from synthorg.api.controllers.learning import LearningController
from synthorg.api.controllers.mcp_catalog import MCPCatalogController
from synthorg.api.controllers.meetings import MeetingController
from synthorg.api.controllers.memory.checkpoints import (
    MemoryCheckpointsController,
)
from synthorg.api.controllers.memory.embedder import MemoryEmbedderController
from synthorg.api.controllers.memory.entries import MemoryEntriesController
from synthorg.api.controllers.memory.fine_tune import MemoryFineTuneController
from synthorg.api.controllers.messages import MessageController
from synthorg.api.controllers.meta import MetaController
from synthorg.api.controllers.meta_analytics import MetaAnalyticsController
from synthorg.api.controllers.metrics import MetricsController
from synthorg.api.controllers.model_refresh import ModelRefreshController
from synthorg.api.controllers.oauth import OAuthController
from synthorg.api.controllers.objectives import ObjectiveController
from synthorg.api.controllers.ontology.admin import OntologyAdminController
from synthorg.api.controllers.ontology.drift import OntologyDriftController
from synthorg.api.controllers.ontology.entities import OntologyController
from synthorg.api.controllers.ontology.versions import OntologyVersionsController
from synthorg.api.controllers.personalities import (
    PersonalityPresetController,
)
from synthorg.api.controllers.project_brain import ProjectBrainController
from synthorg.api.controllers.project_docs import ProjectDocsController
from synthorg.api.controllers.project_knowledge import (
    GlobalKnowledgeController,
    ProjectKnowledgeController,
)
from synthorg.api.controllers.projects import ProjectController
from synthorg.api.controllers.providers.allowlists import (
    ProviderAllowlistsController,
)
from synthorg.api.controllers.providers.audit import ProviderAuditController
from synthorg.api.controllers.providers.capabilities import (
    ProviderCapabilitiesController,
)
from synthorg.api.controllers.providers.connection import (
    ProviderConnectionController,
)
from synthorg.api.controllers.providers.crud import ProviderCrudController
from synthorg.api.controllers.providers.local_models import (
    ProviderLocalModelsController,
)
from synthorg.api.controllers.providers.models import ProviderModelsController
from synthorg.api.controllers.providers.presets import ProviderPresetsController
from synthorg.api.controllers.quality import QualityController
from synthorg.api.controllers.reports import ReportsController
from synthorg.api.controllers.requests.lifecycle import RequestController
from synthorg.api.controllers.reviews import ReviewController
from synthorg.api.controllers.role_versions import RoleVersionController
from synthorg.api.controllers.scaling import ScalingController
from synthorg.api.controllers.settings.core import SettingsCoreController
from synthorg.api.controllers.settings.observability import (
    SettingsObservabilityController,
)
from synthorg.api.controllers.settings.security import SettingsSecurityController
from synthorg.api.controllers.setup.agents import SetupAgentsController
from synthorg.api.controllers.setup.company import SetupCompanyController
from synthorg.api.controllers.setup.completion import SetupCompletionController
from synthorg.api.controllers.setup.locales import SetupLocalesController
from synthorg.api.controllers.setup.status import SetupStatusController
from synthorg.api.controllers.setup_personality import (
    SetupPersonalityController,
)
from synthorg.api.controllers.simulations import SimulationController
from synthorg.api.controllers.ssrf_violations import SsrfViolationController
from synthorg.api.controllers.steering import SteeringController
from synthorg.api.controllers.subworkflows import SubworkflowController
from synthorg.api.controllers.tasks import TaskController
from synthorg.api.controllers.teams import TeamController
from synthorg.api.controllers.template_packs import TemplatePackController
from synthorg.api.controllers.training import TrainingController
from synthorg.api.controllers.tunnel import TunnelController
from synthorg.api.controllers.users.account import UserController
from synthorg.api.controllers.users.org_roles import UserOrgRolesController
from synthorg.api.controllers.webhooks.activity import WebhooksActivityController
from synthorg.api.controllers.webhooks.ingest import WebhooksIngestController
from synthorg.api.controllers.webhooks.retry import WebhooksRetryController
from synthorg.api.controllers.workflow_executions import (
    WorkflowExecutionController,
)
from synthorg.api.controllers.workflow_versions import (
    WorkflowVersionController,
)
from synthorg.api.controllers.workflows.blueprints import (
    WorkflowBlueprintController,
)
from synthorg.api.controllers.workflows.crud import WorkflowController
from synthorg.api.controllers.workflows.validation import (
    WorkflowValidationController,
)
from synthorg.api.controllers.ws import ws_handler
from synthorg.api.state import AppState
from synthorg.client.state import has_simulation_runtime
from synthorg.deliverable_receipts.api_controller import (
    DeliverableReceiptController,
)

# Core API controllers (always registered).
#
# Handlers that resolve the persistence backend through ``persistence_of``
# (including the ``*VersionController`` family) degrade to HTTP 503 when
# persistence is ``None`` via its ``ServiceUnavailableError``.  No
# per-controller gating is required.
BASE_CONTROLLERS: tuple[type[Controller], ...] = (
    LivenessController,
    ReadinessController,
    HealthController,
    MetricsController,
    CapabilitiesController,
    CompanyController,
    AgentCrudController,
    AgentObservabilityController,
    AgentRosterController,
    AgentIdentityVersionController,
    ActivityController,
    DepartmentController,
    DepartmentHealthController,
    DepartmentCeremonyPolicyController,
    ProjectController,
    ObjectiveController,
    BrownfieldController,
    ProjectBrainController,
    ProjectDocsController,
    DeliverableReceiptController,
    ProjectKnowledgeController,
    GlobalKnowledgeController,
    TaskController,
    ExperimentsController,
    MessageController,
    MeetingController,
    ArtifactController,
    CharterController,
    BudgetController,
    ForecastBudgetController,
    ModelRefreshController,
    SsrfViolationController,
    AnalyticsOverviewController,
    AnalyticsTrendsController,
    AnalyticsForecastController,
    ProviderCrudController,
    ProviderConnectionController,
    ProviderModelsController,
    ProviderLocalModelsController,
    ProviderPresetsController,
    ProviderCapabilitiesController,
    ProviderAllowlistsController,
    ProviderAuditController,
    ApprovalsQueryController,
    ApprovalsDecisionsController,
    EscalationsController,
    AutonomyController,
    AuthApiKeysController,
    AuthBootstrapController,
    AuthSessionController,
    AuthCredentialsController,
    AuthIdentityController,
    AuthSessionsController,
    CollaborationController,
    CeremonyPolicyController,
    CoordinationController,
    EventStreamController,
    InterruptController,
    AuditController,
    CoordinationMetricsController,
    SettingsCoreController,
    SettingsObservabilityController,
    SettingsSecurityController,
    SetupStatusController,
    SetupCompanyController,
    SetupAgentsController,
    SetupLocalesController,
    SetupCompletionController,
    SetupPersonalityController,
    PersonalityPresetController,
    BackupController,
    MemoryFineTuneController,
    MemoryCheckpointsController,
    MemoryEntriesController,
    MemoryEmbedderController,
    TeamController,
    TemplatePackController,
    UserController,
    UserOrgRolesController,
    SubworkflowController,
    WorkflowController,
    WorkflowBlueprintController,
    WorkflowValidationController,
    WorkflowVersionController,
    BudgetConfigVersionController,
    CompanyVersionController,
    EvaluationConfigVersionController,
    RoleVersionController,
    QualityController,
    ReportsController,
    WorkflowExecutionController,
    OntologyController,
    OntologyVersionsController,
    OntologyDriftController,
    OntologyAdminController,
    ClientController,
    ReviewController,
    ScalingController,
    TrainingController,
    MetaController,
    ConversationalController,
    MetaAnalyticsController,
    LearningController,
    CustomRuleController,
    CockpitController,
    SteeringController,
)


# Controllers gated by their collaborator service.  These do NOT live
# in ``BASE_CONTROLLERS`` -- they are registered only when their
# dependency is wired so an unconfigured install returns 404 (route
# does not exist) instead of 503 on every dashboard poll.  The web
# dashboard reads ``GET /api/v1/capabilities`` once per session and
# skips polling whichever subsystem reports ``False``.
#
# Each tuple is ``(controller_class, predicate)``; ``app.py`` includes
# the controller in ``route_handlers`` only when the predicate returns
# ``True`` against the live ``AppState`` at controller-list assembly time.
OPTIONAL_CONTROLLERS: tuple[
    tuple[type[Controller], Callable[[AppState], bool]], ...
] = (
    (SimulationController, has_simulation_runtime),
    (RequestController, has_simulation_runtime),
)

# Integration subsystem controllers. Registered only when
# ``effective_config.integrations.enabled`` is True (default in
# production, disabled in unit tests so Litestar route registration
# stays cheap -- ~0.7s per create_app() otherwise).
INTEGRATION_CONTROLLERS: tuple[type[Controller], ...] = (
    ConnectionsController,
    IntegrationHealthController,
    OAuthController,
    WebhooksIngestController,
    WebhooksActivityController,
    WebhooksRetryController,
    MCPCatalogController,
    TunnelController,
)

ALL_CONTROLLERS: tuple[type[Controller], ...] = (
    *BASE_CONTROLLERS,
    *INTEGRATION_CONTROLLERS,
    *(controller for controller, _ in OPTIONAL_CONTROLLERS),
)

__all__ = [
    "ALL_CONTROLLERS",
    "BASE_CONTROLLERS",
    "INTEGRATION_CONTROLLERS",
    "OPTIONAL_CONTROLLERS",
    "ActivityController",
    "AgentCrudController",
    "AgentIdentityVersionController",
    "AgentObservabilityController",
    "AgentRosterController",
    "AnalyticsForecastController",
    "AnalyticsOverviewController",
    "AnalyticsTrendsController",
    "ApprovalsDecisionsController",
    "ApprovalsQueryController",
    "ArtifactController",
    "AuditController",
    "AuthApiKeysController",
    "AuthBootstrapController",
    "AuthCredentialsController",
    "AuthIdentityController",
    "AuthSessionController",
    "AuthSessionsController",
    "AutonomyController",
    "BackupController",
    "BrownfieldController",
    "BudgetConfigVersionController",
    "BudgetController",
    "CeremonyPolicyController",
    "CharterController",
    "ClientController",
    "CollaborationController",
    "CompanyController",
    "CompanyVersionController",
    "ConnectionsController",
    "Controller",
    "ConversationalController",
    "CoordinationController",
    "CoordinationMetricsController",
    "CustomRuleController",
    "DeliverableReceiptController",
    "DepartmentCeremonyPolicyController",
    "DepartmentController",
    "DepartmentHealthController",
    "EscalationsController",
    "EvaluationConfigVersionController",
    "EventStreamController",
    "ExperimentsController",
    "ForecastBudgetController",
    "GlobalKnowledgeController",
    "HealthController",
    "IntegrationHealthController",
    "InterruptController",
    "LearningController",
    "LivenessController",
    "MCPCatalogController",
    "MeetingController",
    "MemoryCheckpointsController",
    "MemoryEmbedderController",
    "MemoryEntriesController",
    "MemoryFineTuneController",
    "MessageController",
    "MetaAnalyticsController",
    "MetaController",
    "MetricsController",
    "ModelRefreshController",
    "OAuthController",
    "OntologyAdminController",
    "OntologyController",
    "OntologyDriftController",
    "OntologyVersionsController",
    "PersonalityPresetController",
    "ProjectBrainController",
    "ProjectController",
    "ProjectDocsController",
    "ProjectKnowledgeController",
    "ProviderAllowlistsController",
    "ProviderAuditController",
    "ProviderCapabilitiesController",
    "ProviderConnectionController",
    "ProviderCrudController",
    "ProviderLocalModelsController",
    "ProviderModelsController",
    "ProviderPresetsController",
    "QualityController",
    "ReadinessController",
    "ReportsController",
    "RequestController",
    "ReviewController",
    "RoleVersionController",
    "ScalingController",
    "SettingsCoreController",
    "SettingsObservabilityController",
    "SettingsSecurityController",
    "SetupAgentsController",
    "SetupCompanyController",
    "SetupCompletionController",
    "SetupLocalesController",
    "SetupPersonalityController",
    "SetupStatusController",
    "SimulationController",
    "SsrfViolationController",
    "SubworkflowController",
    "TaskController",
    "TeamController",
    "TemplatePackController",
    "TrainingController",
    "TunnelController",
    "UserController",
    "UserOrgRolesController",
    "WebhooksActivityController",
    "WebhooksIngestController",
    "WebhooksRetryController",
    "WorkflowBlueprintController",
    "WorkflowController",
    "WorkflowExecutionController",
    "WorkflowValidationController",
    "WorkflowVersionController",
    "ws_handler",
]
