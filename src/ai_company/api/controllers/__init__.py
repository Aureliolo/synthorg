"""API controllers for all resource groups."""

from litestar import Controller

from ai_company.api.auth.controller import AuthController
from ai_company.api.controllers.agents import AgentController
from ai_company.api.controllers.analytics import AnalyticsController
from ai_company.api.controllers.approvals import ApprovalsController
from ai_company.api.controllers.artifacts import ArtifactController
from ai_company.api.controllers.autonomy import AutonomyController
from ai_company.api.controllers.budget import BudgetController
from ai_company.api.controllers.company import CompanyController
from ai_company.api.controllers.departments import DepartmentController
from ai_company.api.controllers.health import HealthController
from ai_company.api.controllers.meetings import MeetingController
from ai_company.api.controllers.messages import MessageController
from ai_company.api.controllers.projects import ProjectController
from ai_company.api.controllers.providers import ProviderController
from ai_company.api.controllers.tasks import TaskController
from ai_company.api.controllers.ws import ws_handler

ALL_CONTROLLERS: tuple[type[Controller], ...] = (
    HealthController,
    CompanyController,
    AgentController,
    DepartmentController,
    ProjectController,
    TaskController,
    MessageController,
    MeetingController,
    ArtifactController,
    BudgetController,
    AnalyticsController,
    ProviderController,
    ApprovalsController,
    AutonomyController,
    AuthController,
)

__all__ = [
    "ALL_CONTROLLERS",
    "AgentController",
    "AnalyticsController",
    "ApprovalsController",
    "ArtifactController",
    "AuthController",
    "AutonomyController",
    "BudgetController",
    "CompanyController",
    "Controller",
    "DepartmentController",
    "HealthController",
    "MeetingController",
    "MessageController",
    "ProjectController",
    "ProviderController",
    "TaskController",
    "ws_handler",
]
