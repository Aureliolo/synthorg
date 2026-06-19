"""Company structure event constants."""

from typing import Final

COMPANY_VALIDATION_ERROR: Final[str] = "company.validation.error"
COMPANY_BUDGET_UNDER_ALLOCATED: Final[str] = "company.budget.under_allocated"

# -- MCP audit events ----------------------------------------------------

COMPANY_UPDATED_VIA_MCP: Final[str] = "organization.company.updated_via_mcp"
DEPARTMENTS_REORDERED_VIA_MCP: Final[str] = "organization.departments.reordered_via_mcp"
DEPARTMENT_CREATED_VIA_MCP: Final[str] = "organization.department.created_via_mcp"
DEPARTMENT_UPDATED_VIA_MCP: Final[str] = "organization.department.updated_via_mcp"
DEPARTMENT_DELETED_VIA_MCP: Final[str] = "organization.department.deleted_via_mcp"
TEAM_CREATED_VIA_MCP: Final[str] = "organization.team.created_via_mcp"
TEAM_UPDATED_VIA_MCP: Final[str] = "organization.team.updated_via_mcp"
TEAM_DELETED_VIA_MCP: Final[str] = "organization.team.deleted_via_mcp"

# -- Capability guards ---------------------------------------------------

ORG_CAPABILITY_UNSUPPORTED: Final[str] = "organization.capability.unsupported"
