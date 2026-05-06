"""Infrastructure domain MCP tools.

Covers health, settings, providers, backup, audit, events, users,
projects, requests, setup, simulations, template packs, and other
infrastructure controllers.
"""

from typing import TYPE_CHECKING

from synthorg.meta.mcp.domains._remaining_args import (
    AuditListArgs,
    BackupCreateArgs,
    BackupDeleteArgs,
    BackupGetArgs,
    BackupListArgs,
    BackupRestoreArgs,
    EventsListArgs,
    HealthCheckArgs,
    IntegrationHealthGetAllArgs,
    IntegrationHealthGetArgs,
    ProjectsCreateArgs,
    ProjectsDeleteArgs,
    ProjectsGetArgs,
    ProjectsListArgs,
    ProjectsUpdateArgs,
    ProvidersGetArgs,
    ProvidersGetHealthArgs,
    ProvidersListArgs,
    ProvidersTestConnectionArgs,
    RequestsCreateArgs,
    RequestsGetArgs,
    RequestsListArgs,
    SettingsDeleteArgs,
    SettingsGetArgs,
    SettingsListArgs,
    SettingsUpdateArgs,
    SetupGetStatusArgs,
    SetupInitializeArgs,
    SimulationsCreateArgs,
    SimulationsGetArgs,
    SimulationsListArgs,
    TemplatePacksGetArgs,
    TemplatePacksInstallArgs,
    TemplatePacksListArgs,
    TemplatePacksUninstallArgs,
    UsersCreateArgs,
    UsersDeleteArgs,
    UsersGetArgs,
    UsersListArgs,
    UsersUpdateArgs,
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

INFRASTRUCTURE_TOOLS: tuple[MCPToolDef, ...] = (
    # --- Health ---
    read_tool(
        "health",
        "check",
        "Get service health status.",
        args_model=HealthCheckArgs,
    ),
    # --- Settings ---
    read_tool(
        "settings",
        "list",
        "List all settings.",
        PAGINATION_PROPERTIES,
        args_model=SettingsListArgs,
    ),
    read_tool(
        "settings",
        "get",
        "Get a setting by key.",
        {
            "key": {"type": "string", "description": "Setting key"},
        },
        required=("key",),
        args_model=SettingsGetArgs,
    ),
    admin_tool(
        "settings",
        "update",
        "Update a setting (admin; requires confirm).",
        {
            "key": {"type": "string", "description": "Setting key"},
            "value": {"type": "string", "description": "New value"},
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=("key", "value", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=SettingsUpdateArgs,
    ),
    admin_tool(
        "settings",
        "delete",
        "Delete a setting (destructive; requires confirm).",
        {
            "key": {"type": "string", "description": "Setting key"},
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=("key", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=SettingsDeleteArgs,
    ),
    # --- Providers ---
    read_tool(
        "providers",
        "list",
        "List configured LLM providers.",
        args_model=ProvidersListArgs,
    ),
    read_tool(
        "providers",
        "get",
        "Get a provider configuration.",
        {
            "provider_name": {"type": "string", "description": "Provider name"},
        },
        required=("provider_name",),
        args_model=ProvidersGetArgs,
    ),
    read_tool(
        "providers",
        "get_health",
        "Get provider health status.",
        {
            "provider_name": {"type": "string", "description": "Provider name"},
        },
        required=("provider_name",),
        args_model=ProvidersGetHealthArgs,
    ),
    admin_tool(
        "providers",
        "test_connection",
        "Test connection to a provider (admin; requires confirm).",
        {
            "provider_name": {"type": "string", "description": "Provider name"},
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=("provider_name", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=ProvidersTestConnectionArgs,
    ),
    # --- Backup ---
    admin_tool(
        "backup",
        "create",
        "Create a backup (admin; requires confirm).",
        ADMIN_GUARDRAIL_PROPERTIES,
        required=ADMIN_GUARDRAIL_REQUIRED,
        args_model=BackupCreateArgs,
    ),
    read_tool("backup", "list", "List available backups.", args_model=BackupListArgs),
    read_tool(
        "backup",
        "get",
        "Get backup details.",
        {
            "backup_id": {"type": "string", "description": "Backup UUID"},
        },
        required=("backup_id",),
        args_model=BackupGetArgs,
    ),
    admin_tool(
        "backup",
        "delete",
        "Delete a backup (destructive; requires confirm).",
        {
            "backup_id": {"type": "string", "description": "Backup UUID"},
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=("backup_id", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=BackupDeleteArgs,
    ),
    admin_tool(
        "backup",
        "restore",
        "Restore from a backup (destructive; requires confirm).",
        {
            "backup_id": {"type": "string", "description": "Backup UUID to restore"},
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=("backup_id", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=BackupRestoreArgs,
    ),
    # --- Audit ---
    read_tool(
        "audit",
        "list",
        "List audit log entries.",
        {
            "agent_id": {"type": "string", "description": "Filter by agent"},
            "tool_name": {"type": "string", "description": "Filter by tool"},
            "action_type": {"type": "string", "description": "Filter by action type"},
            "verdict": {"type": "string", "description": "Filter by verdict"},
            "since": {
                "type": "string",
                "description": "Start datetime (ISO 8601)",
                "format": "date-time",
            },
            "until": {
                "type": "string",
                "description": "End datetime (ISO 8601)",
                "format": "date-time",
            },
            **PAGINATION_PROPERTIES,
        },
        args_model=AuditListArgs,
    ),
    # --- Events ---
    read_tool(
        "events",
        "list",
        "List system events.",
        {
            "event_type": {"type": "string", "description": "Filter by event type"},
            **PAGINATION_PROPERTIES,
        },
        args_model=EventsListArgs,
    ),
    # --- Users ---
    read_tool(
        "users",
        "list",
        "List users.",
        PAGINATION_PROPERTIES,
        args_model=UsersListArgs,
    ),
    read_tool(
        "users",
        "get",
        "Get a user by ID.",
        {
            "user_id": {"type": "string", "description": "User UUID"},
        },
        required=("user_id",),
        args_model=UsersGetArgs,
    ),
    admin_tool(
        "users",
        "create",
        "Create a new user (admin; requires confirm).",
        {
            "username": {"type": "string", "description": "Username"},
            "role": {"type": "string", "description": "User role"},
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=("username", "role", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=UsersCreateArgs,
    ),
    admin_tool(
        "users",
        "update",
        "Update a user (admin; requires confirm).",
        {
            "user_id": {"type": "string", "description": "User UUID"},
            "updates": {
                "type": "object",
                "description": "Fields to update",
                "additionalProperties": False,
                "properties": {
                    "role": {
                        "type": "string",
                        "description": "New access-control role for the user",
                        "minLength": 1,
                        "pattern": r".*\S.*",
                    },
                    "must_change_password": {
                        "type": "boolean",
                        "description": (
                            "Force the user to change password on next login"
                        ),
                    },
                },
            },
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=("user_id", "updates", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=UsersUpdateArgs,
    ),
    admin_tool(
        "users",
        "delete",
        "Delete a user (destructive; requires confirm).",
        {
            "user_id": {"type": "string", "description": "User UUID"},
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=("user_id", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=UsersDeleteArgs,
    ),
    # --- Projects ---
    read_tool(
        "projects",
        "list",
        "List projects.",
        PAGINATION_PROPERTIES,
        args_model=ProjectsListArgs,
    ),
    read_tool(
        "projects",
        "get",
        "Get a project by ID.",
        {
            "project_id": {"type": "string", "description": "Project UUID"},
        },
        required=("project_id",),
        args_model=ProjectsGetArgs,
    ),
    write_tool(
        "projects",
        "create",
        "Create a new project.",
        {
            "name": {"type": "string", "description": "Project name"},
            "description": {"type": "string", "description": "Project description"},
        },
        required=("name",),
        args_model=ProjectsCreateArgs,
    ),
    write_tool(
        "projects",
        "update",
        "Update a project.",
        {
            "project_id": {"type": "string", "description": "Project UUID"},
            "updates": {"type": "object", "description": "Fields to update"},
        },
        required=("project_id", "updates"),
        args_model=ProjectsUpdateArgs,
    ),
    admin_tool(
        "projects",
        "delete",
        "Delete a project (destructive; requires confirm).",
        {
            "project_id": {"type": "string", "description": "Project UUID"},
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=("project_id", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=ProjectsDeleteArgs,
    ),
    # --- Requests ---
    read_tool(
        "requests",
        "list",
        "List agent requests.",
        PAGINATION_PROPERTIES,
        args_model=RequestsListArgs,
    ),
    read_tool(
        "requests",
        "get",
        "Get a request by ID.",
        {
            "request_id": {"type": "string", "description": "Request UUID"},
        },
        required=("request_id",),
        args_model=RequestsGetArgs,
    ),
    write_tool(
        "requests",
        "create",
        "Create a new request.",
        {
            "type": {"type": "string", "description": "Request type"},
            "content": {"type": "string", "description": "Request content"},
        },
        required=("type", "content"),
        args_model=RequestsCreateArgs,
    ),
    # --- Setup ---
    read_tool(
        "setup",
        "get_status",
        "Get setup wizard status.",
        args_model=SetupGetStatusArgs,
    ),
    admin_tool(
        "setup",
        "initialize",
        "Initialize the organization setup (admin; requires confirm).",
        {
            "config": {"type": "object", "description": "Initial configuration"},
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=("config", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=SetupInitializeArgs,
    ),
    # --- Simulations ---
    read_tool(
        "simulations",
        "list",
        "List simulation runs.",
        PAGINATION_PROPERTIES,
        args_model=SimulationsListArgs,
    ),
    read_tool(
        "simulations",
        "get",
        "Get a simulation by ID.",
        {
            "simulation_id": {"type": "string", "description": "Simulation UUID"},
        },
        required=("simulation_id",),
        args_model=SimulationsGetArgs,
    ),
    write_tool(
        "simulations",
        "create",
        "Create and run a simulation.",
        {
            "scenario": {"type": "string", "description": "Simulation scenario"},
            "parameters": {"type": "object", "description": "Simulation parameters"},
        },
        required=("scenario",),
        args_model=SimulationsCreateArgs,
    ),
    # --- Template packs ---
    read_tool(
        "template_packs",
        "list",
        "List available template packs.",
        PAGINATION_PROPERTIES,
        args_model=TemplatePacksListArgs,
    ),
    read_tool(
        "template_packs",
        "get",
        "Get a template pack by ID.",
        {
            "pack_id": {"type": "string", "description": "Template pack UUID"},
        },
        required=("pack_id",),
        args_model=TemplatePacksGetArgs,
    ),
    admin_tool(
        "template_packs",
        "install",
        "Install a template pack (admin; requires confirm).",
        {
            "pack_id": {"type": "string", "description": "Template pack to install"},
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=("pack_id", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=TemplatePacksInstallArgs,
    ),
    admin_tool(
        "template_packs",
        "uninstall",
        "Uninstall a template pack (admin; requires confirm).",
        {
            "pack_id": {"type": "string", "description": "Template pack UUID"},
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=("pack_id", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=TemplatePacksUninstallArgs,
    ),
    # --- Integration health ---
    read_tool(
        "integration_health",
        "get_all",
        "Get health status for all integrations.",
        args_model=IntegrationHealthGetAllArgs,
    ),
    read_tool(
        "integration_health",
        "get",
        "Get health for a specific integration.",
        {
            "integration_name": {"type": "string", "description": "Integration name"},
        },
        required=("integration_name",),
        args_model=IntegrationHealthGetArgs,
    ),
)
