"""API controllers for all resource groups."""

from typing import TYPE_CHECKING

from litestar import Controller

from synthorg.api.auth.controllers.bootstrap import AuthBootstrapController
from synthorg.api.auth.controllers.credentials import AuthCredentialsController
from synthorg.api.auth.controllers.identity import AuthIdentityController
from synthorg.api.auth.controllers.session import AuthSessionController
from synthorg.api.auth.controllers.sessions_mgmt import AuthSessionsController
from synthorg.api.controllers.activities import ActivityController
from synthorg.api.controllers.agent_identity_versions import (
    AgentIdentityVersionController,
)
from synthorg.api.controllers.agents import AgentController
from synthorg.api.controllers.analytics import AnalyticsController
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
from synthorg.api.controllers.oauth import OAuthController
from synthorg.api.controllers.objectives import ObjectiveController
from synthorg.api.controllers.ontology import OntologyController
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
from synthorg.api.controllers.requests import RequestController
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
from synthorg.api.controllers.subworkflows import SubworkflowController
from synthorg.api.controllers.tasks import TaskController
from synthorg.api.controllers.teams import TeamController
from synthorg.api.controllers.template_packs import TemplatePackController
from synthorg.api.controllers.training import TrainingController
from synthorg.api.controllers.tunnel import TunnelController
from synthorg.api.controllers.users import UserController
from synthorg.api.controllers.webhooks.activity import WebhooksActivityController
from synthorg.api.controllers.webhooks.ingest import WebhooksIngestController
from synthorg.api.controllers.webhooks.retry import WebhooksRetryController
from synthorg.api.controllers.workflow_executions import (
    WorkflowExecutionController,
)
from synthorg.api.controllers.workflow_versions import (
    WorkflowVersionController,
)
from synthorg.api.controllers.workflows import WorkflowController
from synthorg.api.controllers.ws import ws_handler
from synthorg.client.state import has_simulation_runtime

# Core API controllers (always registered).
#
# Handlers that resolve the persistence backend through ``persistence_of``
# (including the ``*VersionController`` family) degrade to HTTP 503 when
# persistence is ``None`` via its ``ServiceUnavailableError``.  No
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
    ProjectBrainController,
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
    LearningController,
    CustomRuleController,
    CockpitController,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from synthorg.api.state import AppState


def _has_objective_entry_adapter(app_state: AppState) -> bool:
    """Report whether the objective work-entry adapter is wired.

    Returns:
        ``True`` when the objective entry adapter is composed.
    """
    from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415

    return app_state.slice(EngineStateSlice).objective_entry_adapter is not None


def _has_brownfield_entry_adapter(app_state: AppState) -> bool:
    """Report whether the brownfield work-entry adapter is wired.

    Returns:
        ``True`` when the brownfield entry adapter is composed.
    """
    from synthorg.engine.state import EngineStateSlice  # noqa: PLC0415

    return app_state.slice(EngineStateSlice).brownfield_entry_adapter is not None


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
    (ObjectiveController, _has_objective_entry_adapter),
    (BrownfieldController, _has_brownfield_entry_adapter),
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
    "AgentController",
    "AgentIdentityVersionController",
    "AnalyticsController",
    "ApprovalsDecisionsController",
    "ApprovalsQueryController",
    "ArtifactController",
    "AuditController",
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
    "OAuthController",
    "OntologyController",
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
    "SubworkflowController",
    "TaskController",
    "TeamController",
    "TemplatePackController",
    "TrainingController",
    "TunnelController",
    "UserController",
    "WebhooksActivityController",
    "WebhooksIngestController",
    "WebhooksRetryController",
    "WorkflowController",
    "WorkflowExecutionController",
    "WorkflowVersionController",
    "ws_handler",
]
