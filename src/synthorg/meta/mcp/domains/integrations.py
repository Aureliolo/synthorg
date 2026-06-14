"""Integrations domain MCP tools.

Covers MCP catalog, OAuth, clients, artifacts, and ontology.
"""

from typing import TYPE_CHECKING

from synthorg.meta.mcp.domains._remaining_args import (
    ArtifactsCreateArgs,
    ArtifactsDeleteArgs,
    ArtifactsGetArgs,
    ArtifactsListArgs,
    ClientsCreateArgs,
    ClientsDeactivateArgs,
    ClientsGetArgs,
    ClientsGetSatisfactionArgs,
    ClientsListArgs,
    McpCatalogGetArgs,
    McpCatalogInstallArgs,
    McpCatalogListArgs,
    McpCatalogSearchArgs,
    McpCatalogUninstallArgs,
    OauthConfigureProviderArgs,
    OauthListProvidersArgs,
    OauthRemoveProviderArgs,
    OntologyGetEntityArgs,
    OntologyGetRelationshipsArgs,
    OntologyListEntitiesArgs,
    OntologySearchArgs,
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

INTEGRATION_TOOLS: tuple[MCPToolDef, ...] = (
    # --- MCP catalog ---
    read_tool(
        "mcp_catalog",
        "list",
        "List available MCP server catalog entries.",
        PAGINATION_PROPERTIES,
        args_model=McpCatalogListArgs,
    ),
    read_tool(
        "mcp_catalog",
        "search",
        "Search the MCP catalog.",
        {
            "query": {"type": "string", "description": "Search query"},
        },
        required=("query",),
        args_model=McpCatalogSearchArgs,
    ),
    read_tool(
        "mcp_catalog",
        "get",
        "Get an MCP catalog entry by ID.",
        {
            "entry_id": {"type": "string", "description": "Catalog entry ID"},
        },
        required=("entry_id",),
        args_model=McpCatalogGetArgs,
    ),
    admin_tool(
        "mcp_catalog",
        "install",
        "Install an MCP server from the catalog.",
        {
            "entry_id": {"type": "string", "description": "Catalog entry to install"},
        },
        required=("entry_id",),
        args_model=McpCatalogInstallArgs,
    ),
    admin_tool(
        "mcp_catalog",
        "uninstall",
        "Uninstall an MCP server.",
        {
            "installation_id": {"type": "string", "description": "Installation ID"},
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=("installation_id", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=McpCatalogUninstallArgs,
    ),
    # --- OAuth ---
    read_tool(
        "oauth",
        "list_providers",
        "List configured OAuth providers.",
        args_model=OauthListProvidersArgs,
    ),
    admin_tool(
        "oauth",
        "configure_provider",
        "Configure an OAuth provider.",
        {
            "name": {"type": "string", "description": "Provider name"},
            "client_id": {"type": "string", "description": "OAuth client ID"},
            "authorize_url": {
                "type": "string",
                "description": "Authorization endpoint URL",
            },
            "token_url": {"type": "string", "description": "Token endpoint URL"},
            "scopes": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Requested OAuth scopes",
            },
        },
        required=("name", "client_id", "authorize_url", "token_url"),
        args_model=OauthConfigureProviderArgs,
    ),
    admin_tool(
        "oauth",
        "remove_provider",
        "Remove an OAuth provider.",
        {
            "name": {"type": "string", "description": "Provider name"},
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=("name", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=OauthRemoveProviderArgs,
    ),
    # --- Clients ---
    read_tool(
        "clients",
        "list",
        "List API clients.",
        PAGINATION_PROPERTIES,
        args_model=ClientsListArgs,
    ),
    read_tool(
        "clients",
        "get",
        "Get an API client by ID.",
        {
            "client_id": {"type": "string", "description": "Client UUID"},
        },
        required=("client_id",),
        args_model=ClientsGetArgs,
    ),
    admin_tool(
        "clients",
        "create",
        "Create a new API client.",
        {
            "name": {"type": "string", "description": "Client name"},
            "contact_email": {
                "type": "string",
                "description": "Primary contact email",
            },
            "notes": {"type": "string", "description": "Free-form notes"},
        },
        required=("name",),
        args_model=ClientsCreateArgs,
    ),
    admin_tool(
        "clients",
        "deactivate",
        "Deactivate an API client.",
        {
            "client_id": {"type": "string", "description": "Client UUID"},
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=("client_id", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=ClientsDeactivateArgs,
    ),
    read_tool(
        "clients",
        "get_satisfaction",
        "Get client satisfaction score.",
        {
            "client_id": {"type": "string", "description": "Client UUID"},
        },
        required=("client_id",),
        args_model=ClientsGetSatisfactionArgs,
    ),
    # --- Artifacts ---
    read_tool(
        "artifacts",
        "list",
        "List artifacts with optional filtering.",
        {
            "task_id": {"type": "string", "description": "Filter by task"},
            "created_by": {"type": "string", "description": "Filter by creator"},
            "type": {"type": "string", "description": "Filter by artifact type"},
            **PAGINATION_PROPERTIES,
        },
        args_model=ArtifactsListArgs,
    ),
    read_tool(
        "artifacts",
        "get",
        "Get an artifact by ID.",
        {
            "artifact_id": {"type": "string", "description": "Artifact UUID"},
        },
        required=("artifact_id",),
        args_model=ArtifactsGetArgs,
    ),
    write_tool(
        "artifacts",
        "create",
        "Create a new artifact.",
        {
            "name": {"type": "string", "description": "Artifact name"},
            "content_type": {"type": "string", "description": "MIME content type"},
            "size_bytes": {
                "type": "integer",
                "description": "Artifact size in bytes",
                "minimum": 0,
            },
            "storage_ref": {
                "type": "string",
                "description": "Storage backend reference",
            },
        },
        required=("name", "content_type", "size_bytes", "storage_ref"),
        args_model=ArtifactsCreateArgs,
    ),
    admin_tool(
        "artifacts",
        "delete",
        "Delete an artifact (destructive; requires confirm).",
        {
            "artifact_id": {"type": "string", "description": "Artifact UUID"},
            **ADMIN_GUARDRAIL_PROPERTIES,
        },
        required=("artifact_id", *ADMIN_GUARDRAIL_REQUIRED),
        args_model=ArtifactsDeleteArgs,
    ),
    # --- Ontology ---
    read_tool(
        "ontology",
        "list_entities",
        "List ontology entities.",
        PAGINATION_PROPERTIES,
        args_model=OntologyListEntitiesArgs,
    ),
    read_tool(
        "ontology",
        "get_entity",
        "Get an ontology entity by ID.",
        {
            "entity_id": {"type": "string", "description": "Entity ID"},
        },
        required=("entity_id",),
        args_model=OntologyGetEntityArgs,
    ),
    read_tool(
        "ontology",
        "get_relationships",
        "Get relationships for an entity.",
        {
            "entity_id": {"type": "string", "description": "Entity ID"},
        },
        required=("entity_id",),
        args_model=OntologyGetRelationshipsArgs,
    ),
    read_tool(
        "ontology",
        "search",
        "Search the ontology.",
        {
            "query": {"type": "string", "description": "Search query"},
        },
        required=("query",),
        args_model=OntologySearchArgs,
    ),
)
