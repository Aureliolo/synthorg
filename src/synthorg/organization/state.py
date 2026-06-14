"""Organization feature state slice.

Holds the org-structure read / mutation services: company read,
department, role-version, and team services. All are wired lazily once
persistence is connected and are ``None`` until then; readers guard
accordingly.
"""

from typing import TYPE_CHECKING

from pydantic import ConfigDict

from synthorg._core.features import BaseFeatureStateSlice, require_service
from synthorg.organization.services import (
    CompanyReadService,
    DepartmentService,
    RoleVersionService,
    TeamService,
)

if TYPE_CHECKING:
    from synthorg.api.state_slices import AppStateSliceMixin


class OrganizationStateSlice(BaseFeatureStateSlice):
    """Application-state slice owned by the organization feature."""

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    company_read_service: CompanyReadService | None = None
    department_service: DepartmentService | None = None
    role_version_service: RoleVersionService | None = None
    team_service: TeamService | None = None


def company_read_service_of(app_state: AppStateSliceMixin) -> CompanyReadService:
    """Resolve the company read service from its slice, or raise 503.

    Returns:
        The wired company read service.
    """
    return require_service(
        app_state.slice(OrganizationStateSlice).company_read_service,
        "Company Read Service",
    )


def department_service_of(app_state: AppStateSliceMixin) -> DepartmentService:
    """Resolve the department service from its slice, or raise 503.

    Returns:
        The wired department service.
    """
    return require_service(
        app_state.slice(OrganizationStateSlice).department_service,
        "Department Service",
    )


def role_version_service_of(app_state: AppStateSliceMixin) -> RoleVersionService:
    """Resolve the role version service from its slice, or raise 503.

    Returns:
        The wired role version service.
    """
    return require_service(
        app_state.slice(OrganizationStateSlice).role_version_service,
        "Role Version Service",
    )


def team_service_of(app_state: AppStateSliceMixin) -> TeamService:
    """Resolve the team service from its slice, or raise 503.

    Returns:
        The wired team service.
    """
    return require_service(
        app_state.slice(OrganizationStateSlice).team_service, "Team Service"
    )
