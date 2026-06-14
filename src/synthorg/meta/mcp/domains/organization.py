"""Organization domain MCP tools.

Covers company, company versions, departments, teams, and role versions.
"""

from typing import TYPE_CHECKING

from synthorg.meta.mcp.domains._workflows_org_args import (
    CompanyGetArgs,
    CompanyListDepartmentsArgs,
    CompanyReorderDepartmentsArgs,
    CompanyUpdateArgs,
    CompanyVersionsGetArgs,
    CompanyVersionsListArgs,
    DepartmentsCreateArgs,
    DepartmentsDeleteArgs,
    DepartmentsGetArgs,
    DepartmentsGetHealthArgs,
    DepartmentsListArgs,
    DepartmentsUpdateArgs,
    RoleVersionsGetArgs,
    RoleVersionsListArgs,
    TeamsCreateArgs,
    TeamsDeleteArgs,
    TeamsGetArgs,
    TeamsListArgs,
    TeamsUpdateArgs,
)
from synthorg.meta.mcp.tool_builder import (
    ADMIN_GUARDRAIL_PROPERTIES,
    ADMIN_GUARDRAIL_REQUIRED,
    PAGINATION_PROPERTIES,
    admin_tool,
    read_tool,
    write_tool,
)

if TYPE_CHECKING:
    from synthorg.meta.mcp.registry import MCPToolDef

ORGANIZATION_TOOLS: tuple[MCPToolDef, ...] = (
    # --- Company ---
    read_tool(
        "company",
        "get",
        "Get the company configuration.",
        args_model=CompanyGetArgs,
    ),
    write_tool(
        "company",
        "update",
        "Update company configuration.",
        {
            "payload": {
                "type": "object",
                "description": "Company-record patch payload",
            },
        },
        required=("payload",),
        args_model=CompanyUpdateArgs,
    ),
    read_tool(
        "company",
        "list_departments",
        "List departments in the company.",
        args_model=CompanyListDepartmentsArgs,
    ),
    write_tool(
        "company",
        "reorder_departments",
        "Reorder departments.",
        {
            "department_ids": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "description": "Department IDs in display order",
            },
        },
        required=("department_ids",),
        args_model=CompanyReorderDepartmentsArgs,
    ),
    # --- Company versions ---
    read_tool(
        "company_versions",
        "list",
        "List company configuration versions.",
        PAGINATION_PROPERTIES,
        args_model=CompanyVersionsListArgs,
    ),
    read_tool(
        "company_versions",
        "get",
        "Get a specific company config version.",
        {
            "version_id": {
                "type": "string",
                "description": "Company version ID",
                "minLength": 1,
            },
        },
        required=("version_id",),
        args_model=CompanyVersionsGetArgs,
    ),
    # --- Departments ---
    read_tool(
        "departments",
        "list",
        "List departments with pagination.",
        PAGINATION_PROPERTIES,
        args_model=DepartmentsListArgs,
    ),
    read_tool(
        "departments",
        "get",
        "Get a department by ID.",
        {
            "department_id": {"type": "string", "description": "Department ID"},
        },
        required=("department_id",),
        args_model=DepartmentsGetArgs,
    ),
    write_tool(
        "departments",
        "create",
        "Create a new department.",
        {
            "name": {"type": "string", "description": "Department name"},
            "description": {
                "type": "string",
                "description": "Department description",
                "minLength": 1,
            },
        },
        required=("name", "description"),
        args_model=DepartmentsCreateArgs,
    ),
    write_tool(
        "departments",
        "update",
        "Update a department.",
        {
            "department_id": {"type": "string", "description": "Department ID"},
            "name": {"type": "string", "description": "New name"},
            "description": {"type": "string", "description": "New description"},
        },
        required=("department_id",),
        args_model=DepartmentsUpdateArgs,
    ),
    admin_tool(
        "departments",
        "delete",
        "Delete a department (destructive; requires confirm).",
        {
            "department_id": {
                "type": "string",
                "description": "Department ID",
                "minLength": 1,
            },
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=("department_id", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=DepartmentsDeleteArgs,
    ),
    read_tool(
        "departments",
        "get_health",
        "Get department health status.",
        {
            "department_id": {"type": "string", "description": "Department ID"},
        },
        required=("department_id",),
        args_model=DepartmentsGetHealthArgs,
    ),
    # --- Teams ---
    read_tool(
        "teams",
        "list",
        "List teams with pagination.",
        PAGINATION_PROPERTIES,
        args_model=TeamsListArgs,
    ),
    read_tool(
        "teams",
        "get",
        "Get a team by ID.",
        {
            "team_id": {"type": "string", "description": "Team UUID"},
        },
        required=("team_id",),
        args_model=TeamsGetArgs,
    ),
    write_tool(
        "teams",
        "create",
        "Create a new team.",
        {
            "name": {"type": "string", "description": "Team name"},
            "department_id": {
                "type": "string",
                "description": "Parent department ID",
            },
        },
        required=("name",),
        args_model=TeamsCreateArgs,
    ),
    write_tool(
        "teams",
        "update",
        "Update a team.",
        {
            "team_id": {"type": "string", "description": "Team UUID"},
            "name": {"type": "string", "description": "New name"},
            "department_id": {
                "type": "string",
                "description": "New parent department ID",
            },
        },
        required=("team_id",),
        args_model=TeamsUpdateArgs,
    ),
    admin_tool(
        "teams",
        "delete",
        "Delete a team (destructive; requires confirm).",
        {
            "team_id": {
                "type": "string",
                "description": "Team UUID",
                "minLength": 1,
            },
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=("team_id", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=TeamsDeleteArgs,
    ),
    # --- Role versions ---
    read_tool(
        "role_versions",
        "list",
        "List role configuration versions.",
        {
            "role_name": {"type": "string", "description": "Filter by role name"},
            **PAGINATION_PROPERTIES,
        },
        args_model=RoleVersionsListArgs,
    ),
    read_tool(
        "role_versions",
        "get",
        "Get a specific role version.",
        {
            "version_id": {
                "type": "string",
                "description": "Role version ID",
                "minLength": 1,
            },
        },
        required=("version_id",),
        args_model=RoleVersionsGetArgs,
    ),
)
