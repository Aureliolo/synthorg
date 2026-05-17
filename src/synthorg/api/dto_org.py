"""Request DTOs for company, department, and agent mutation endpoints.

The request models live in :mod:`synthorg.organization.models` so the
organization service layer can validate the same input shapes without
importing from the API layer (the persistence/service layers are
forbidden from importing ``synthorg.api.dto_*``). This module re-exports
them for HTTP controllers.
"""

from synthorg.organization.models import (
    CreateAgentOrgRequest,
    CreateDepartmentRequest,
    ReorderAgentsRequest,
    ReorderDepartmentsRequest,
    UpdateAgentOrgRequest,
    UpdateCompanyRequest,
    UpdateDepartmentRequest,
)

__all__ = [
    "CreateAgentOrgRequest",
    "CreateDepartmentRequest",
    "ReorderAgentsRequest",
    "ReorderDepartmentsRequest",
    "UpdateAgentOrgRequest",
    "UpdateCompanyRequest",
    "UpdateDepartmentRequest",
]
