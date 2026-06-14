"""Integrations-domain MCP args.

Covers mcp_catalog, oauth, clients, artifacts, ontology.
"""

from pydantic import Field

from synthorg.core.types import NotBlankStr
from synthorg.meta.mcp.domains._common_args import (
    AdminGuardrailFields,
    PaginationFields,
    _ArgsBase,
)


class McpCatalogListArgs(PaginationFields):
    """Args for ``mcp_catalog.list``."""


class McpCatalogSearchArgs(_ArgsBase):
    """Args for ``mcp_catalog.search``."""

    query: NotBlankStr = Field(description="Search query")


class McpCatalogGetArgs(_ArgsBase):
    """Args for ``mcp_catalog.get``."""

    entry_id: NotBlankStr = Field(description="Catalog entry ID")


class McpCatalogInstallArgs(_ArgsBase):
    """Args for ``mcp_catalog.install``."""

    entry_id: NotBlankStr = Field(description="Catalog entry to install")


class McpCatalogUninstallArgs(AdminGuardrailFields):
    """Args for ``mcp_catalog.uninstall``.

    Destructive admin op: callers supply ``confirm=True`` and a non-blank
    ``reason`` (mixin) alongside the installation UUID.
    """

    installation_id: NotBlankStr = Field(description="Installation ID")


class OauthListProvidersArgs(_ArgsBase):
    """Args for ``oauth.list_providers``: no fields."""


class OauthConfigureProviderArgs(_ArgsBase):
    """Args for ``oauth.configure_provider``."""

    name: NotBlankStr = Field(description="Provider name")
    client_id: NotBlankStr = Field(description="OAuth client ID")
    authorize_url: NotBlankStr = Field(description="Authorization endpoint URL")
    token_url: NotBlankStr = Field(description="Token endpoint URL")
    scopes: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Requested OAuth scopes",
    )


class OauthRemoveProviderArgs(AdminGuardrailFields):
    """Args for ``oauth.remove_provider`` (destructive admin op)."""

    name: NotBlankStr = Field(description="Provider name")


class ClientsListArgs(PaginationFields):
    """Args for ``clients.list``."""


class _ClientIdArgs(_ArgsBase):
    """Mixin for tools keyed by ``client_id``."""

    client_id: NotBlankStr = Field(description="Client UUID")


class ClientsGetArgs(_ClientIdArgs):
    """Args for ``clients.get``."""


class ClientsCreateArgs(_ArgsBase):
    """Args for ``clients.create``."""

    name: NotBlankStr = Field(description="Client name")
    contact_email: NotBlankStr | None = Field(
        default=None,
        description="Primary contact email",
    )
    notes: NotBlankStr | None = Field(
        default=None,
        description="Free-form notes",
    )


class ClientsDeactivateArgs(_ClientIdArgs, AdminGuardrailFields):
    """Args for ``clients.deactivate`` (destructive admin op)."""


class ClientsGetSatisfactionArgs(_ClientIdArgs):
    """Args for ``clients.get_satisfaction``."""


class ArtifactsListArgs(PaginationFields):
    """Args for ``artifacts.list``."""

    task_id: NotBlankStr | None = Field(default=None, description="Filter by task")
    created_by: NotBlankStr | None = Field(
        default=None,
        description="Filter by creator",
    )
    type: NotBlankStr | None = Field(
        default=None,
        description="Filter by artifact type",
    )


class ArtifactsGetArgs(_ArgsBase):
    """Args for ``artifacts.get``."""

    artifact_id: NotBlankStr = Field(description="Artifact UUID")


class ArtifactsCreateArgs(_ArgsBase):
    """Args for ``artifacts.create``."""

    name: NotBlankStr = Field(description="Artifact name")
    content_type: NotBlankStr = Field(description="MIME content type")
    size_bytes: int = Field(ge=0, description="Artifact size in bytes")
    storage_ref: NotBlankStr = Field(description="Storage backend reference")


class ArtifactsDeleteArgs(AdminGuardrailFields):
    """Args for ``artifacts.delete``.

    Destructive admin op: callers must supply ``confirm=True`` and a
    non-blank ``reason`` (mixin), in addition to the artifact UUID.
    """

    artifact_id: NotBlankStr = Field(description="Artifact UUID")


class OntologyListEntitiesArgs(PaginationFields):
    """Args for ``ontology.list_entities``."""


class _EntityIdArgs(_ArgsBase):
    """Mixin for tools keyed by ``entity_id``."""

    entity_id: NotBlankStr = Field(description="Entity ID")


class OntologyGetEntityArgs(_EntityIdArgs):
    """Args for ``ontology.get_entity``."""


class OntologyGetRelationshipsArgs(_EntityIdArgs):
    """Args for ``ontology.get_relationships``."""


class OntologySearchArgs(_ArgsBase):
    """Args for ``ontology.search``."""

    query: NotBlankStr = Field(description="Search query")
