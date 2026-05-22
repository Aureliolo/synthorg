"""API controllers for all resource groups."""

from litestar import Controller

from synthorg.api.auth.controller import AuthController
from synthorg.api.controllers.activities import ActivityController
from synthorg.api.controllers.agent_identity_versions import (
    AgentIdentityVersionController,
)
from synthorg.api.controllers.agents import AgentController
from synthorg.api.controllers.analytics import AnalyticsController
from synthorg.api.controllers.approvals import ApprovalsController
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
from synthorg.api.controllers.coordination import CoordinationController
from synthorg.api.controllers.coordination_metrics import (
    CoordinationMetricsController,
)
from synthorg.api.controllers.custom_rules import CustomRuleController
from synthorg.api.controllers.departments import DepartmentController
from synthorg.api.controllers.escalations import EscalationsController
from synthorg.api.controllers.evaluation_config_versions import (
    EvaluationConfigVersionController,
)
from synthorg.api.controllers.events import (
    EventStreamController,
    InterruptController,
)
from synthorg.api.controllers.experiments import ExperimentsController
from synthorg.api.controllers.health import (
    LivenessController,
    ReadinessController,
)
from synthorg.api.controllers.integration_health import (
    IntegrationHealthController,
)
from synthorg.api.controllers.mcp_catalog import MCPCatalogController
from synthorg.api.controllers.meetings import MeetingController
from synthorg.api.controllers.memory import MemoryAdminController
from synthorg.api.controllers.messages import MessageController
from synthorg.api.controllers.meta import MetaController
from synthorg.api.controllers.meta_analytics import MetaAnalyticsController
from synthorg.api.controllers.metrics import MetricsController
from synthorg.api.controllers.oauth import OAuthController
from synthorg.api.controllers.objectives import ObjectiveController
from synthorg.api.controllers.ontology import OntologyController
from synthorg.api.controllers.personalities import (
    PersonalityPresetController,
)
from synthorg.api.controllers.project_docs import ProjectDocsController
from synthorg.api.controllers.project_knowledge import (
    GlobalKnowledgeController,
    ProjectKnowledgeController,
)
from synthorg.api.controllers.projects import ProjectController
from synthorg.api.controllers.providers import ProviderController
from synthorg.api.controllers.quality import QualityController
from synthorg.api.controllers.reports import ReportsController
from synthorg.api.controllers.requests import RequestController
from synthorg.api.controllers.reviews import ReviewController
from synthorg.api.controllers.role_versions import RoleVersionController
from synthorg.api.controllers.scaling import ScalingController
from synthorg.api.controllers.settings import SettingsController
from synthorg.api.controllers.setup_controller import SetupController
from synthorg.api.controllers.setup_personality import (
    SetupPersonalityController,
)
from synthorg.api.controllers.simulations import SimulationController
from synthorg.api.controllers.subworkflows import SubworkflowController
from synthorg.api.controllers.tasks import TaskController
from synthorg.api.controllers.teams import TeamController
from synthorg.api.controllers.template_packs import TemplatePackController
from synthorg.api.controllers.training import TrainingController
from synthorg.api.controllers.tunnel import TunnelController
from synthorg.api.controllers.users import UserController
from synthorg.api.controllers.webhooks import WebhooksController
from synthorg.api.controllers.workflow_executions import (
    WorkflowExecutionController,
)
from synthorg.api.controllers.workflow_versions import (
    WorkflowVersionController,
)
from synthorg.api.controllers.workflows import WorkflowController
from synthorg.api.controllers.ws import ws_handler

# Core API controllers (always registered).
#
# Handlers that dereference ``state.app_state.persistence`` (including the
# ``*VersionController`` family) degrade to HTTP 503 when persistence is
# ``None`` via ``AppState.persistence``'s ``ServiceUnavailableError``.  No
# per-controller gating is required.
BASE_CONTROLLERS: tuple[type[Controller], ...] = (
    LivenessController,
    ReadinessController,
    MetricsController,
    CapabilitiesController,
    CompanyController,
    AgentController,
    AgentIdentityVersionController,
    ActivityController,
    DepartmentController,
    ProjectController,
    ProjectDocsController,
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
    AnalyticsController,
    ProviderController,
    ApprovalsController,
    EscalationsController,
    AutonomyController,
    AuthController,
    CollaborationController,
    CeremonyPolicyController,
    CoordinationController,
    EventStreamController,
    InterruptController,
    AuditController,
    CoordinationMetricsController,
    SettingsController,
    SetupController,
    SetupPersonalityController,
    PersonalityPresetController,
    BackupController,
    MemoryAdminController,
    TeamController,
    TemplatePackController,
    UserController,
    SubworkflowController,
    WorkflowController,
    WorkflowVersionController,
    BudgetConfigVersionController,
    CompanyVersionController,
    EvaluationConfigVersionController,
    RoleVersionController,
    QualityController,
    ReportsController,
    WorkflowExecutionController,
    OntologyController,
    ClientController,
    ReviewController,
    ScalingController,
    TrainingController,
    MetaController,
    MetaAnalyticsController,
    CustomRuleController,
    CockpitController,
)

# Controllers gated by their collaborator service.  These do NOT live
# in ``BASE_CONTROLLERS`` -- they are registered only when their
# dependency is wired so an unconfigured install returns 404 (route
# does not exist) instead of 503 on every dashboard poll.  The web
# dashboard reads ``GET /api/v1/capabilities`` once per session and
# skips polling whichever subsystem reports ``False``.
#
# Each tuple is ``(controller_class, predicate_attribute_on_appstate)``;
# ``app.py`` includes the controller in ``route_handlers`` only when
# the predicate evaluates truthy at controller-list assembly time.
OPTIONAL_CONTROLLERS: tuple[tuple[type[Controller], str], ...] = (
    (SimulationController, "has_simulation_runtime"),
    (RequestController, "has_simulation_runtime"),
    (ObjectiveController, "has_objective_entry_adapter"),
    (BrownfieldController, "has_brownfield_entry_adapter"),
)

# Integration subsystem controllers. Registered only when
# ``effective_config.integrations.enabled`` is True (default in
# production, disabled in unit tests so Litestar route registration
# stays cheap -- ~0.7s per create_app() otherwise).
INTEGRATION_CONTROLLERS: tuple[type[Controller], ...] = (
    ConnectionsController,
    IntegrationHealthController,
    OAuthController,
    WebhooksController,
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
    "AgentController",
    "AgentIdentityVersionController",
    "AnalyticsController",
    "ApprovalsController",
    "ArtifactController",
    "AuditController",
    "AuthController",
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
    "CoordinationController",
    "CoordinationMetricsController",
    "CustomRuleController",
    "DepartmentController",
    "EscalationsController",
    "EvaluationConfigVersionController",
    "EventStreamController",
    "ExperimentsController",
    "ForecastBudgetController",
    "GlobalKnowledgeController",
    "IntegrationHealthController",
    "InterruptController",
    "LivenessController",
    "MCPCatalogController",
    "MeetingController",
    "MemoryAdminController",
    "MessageController",
    "MetaAnalyticsController",
    "MetaController",
    "MetricsController",
    "OAuthController",
    "OntologyController",
    "PersonalityPresetController",
    "ProjectController",
    "ProjectDocsController",
    "ProjectKnowledgeController",
    "ProviderController",
    "QualityController",
    "ReadinessController",
    "ReportsController",
    "RequestController",
    "ReviewController",
    "RoleVersionController",
    "ScalingController",
    "SettingsController",
    "SetupController",
    "SetupPersonalityController",
    "SimulationController",
    "SubworkflowController",
    "TaskController",
    "TeamController",
    "TemplatePackController",
    "TrainingController",
    "TunnelController",
    "UserController",
    "WebhooksController",
    "WorkflowController",
    "WorkflowExecutionController",
    "WorkflowVersionController",
    "ws_handler",
]
