"""API controllers for all resource groups."""

from litestar import Controller

from synthorg.api.auth.controller import AuthController
from synthorg.api.controllers.agents import AgentController
from synthorg.api.controllers.analytics import AnalyticsController
from synthorg.api.controllers.approvals import ApprovalsController
from synthorg.api.controllers.artifacts import ArtifactController
from synthorg.api.controllers.autonomy import AutonomyController
from synthorg.api.controllers.budget import BudgetController
from synthorg.api.controllers.company import CompanyController
from synthorg.api.controllers.coordination import CoordinationController
from synthorg.api.controllers.departments import DepartmentController
from synthorg.api.controllers.health import HealthController
from synthorg.api.controllers.meetings import MeetingController
from synthorg.api.controllers.messages import MessageController
from synthorg.api.controllers.projects import ProjectController
from synthorg.api.controllers.providers import ProviderController
from synthorg.api.controllers.tasks import TaskController
from synthorg.api.controllers.ws import ws_handler

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
    CoordinationController,
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
    "CoordinationController",
    "DepartmentController",
    "HealthController",
    "MeetingController",
    "MessageController",
    "ProjectController",
    "ProviderController",
    "TaskController",
    "ws_handler",
]
