"""Integrations-domain MCP args.

Covers mcp_catalog, oauth, clients, artifacts, ontology.
"""

from pydantic import Field

from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic field type
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

    catalog_id: NotBlankStr = Field(description="Catalog entry ID")


class McpCatalogInstallArgs(_ArgsBase):
    """Args for ``mcp_catalog.install``."""

    catalog_id: NotBlankStr = Field(description="Catalog entry to install")


class McpCatalogUninstallArgs(_ArgsBase):
    """Args for ``mcp_catalog.uninstall``."""

    install_id: NotBlankStr = Field(description="Installation ID")


class OauthListProvidersArgs(_ArgsBase):
    """Args for ``oauth.list_providers``: no fields."""


class OauthConfigureProviderArgs(_ArgsBase):
    """Args for ``oauth.configure_provider``."""

    provider: NotBlankStr = Field(description="Provider name")
    config: dict[str, object] = Field(description="OAuth configuration")


class OauthRemoveProviderArgs(_ArgsBase):
    """Args for ``oauth.remove_provider``."""

    provider: NotBlankStr = Field(description="Provider name")


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


class ClientsDeactivateArgs(_ClientIdArgs):
    """Args for ``clients.deactivate``."""


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

    type: NotBlankStr = Field(description="Artifact type")
    content: str = Field(description="Artifact content")
    task_id: NotBlankStr | None = Field(default=None, description="Associated task")


class ArtifactsDeleteArgs(AdminGuardrailFields):
    """Args for ``artifacts.delete``.

    Destructive admin op: callers must supply ``confirm=True`` and a
    non-blank ``reason`` (mixin), in addition to the artifact UUID.
    """

    artifact_id: NotBlankStr = Field(description="Artifact UUID")


class OntologyListEntitiesArgs(PaginationFields):
    """Args for ``ontology.list_entities``."""


class _EntityNameArgs(_ArgsBase):
    """Mixin for tools keyed by ``entity_name``."""

    entity_name: NotBlankStr = Field(description="Entity name")


class OntologyGetEntityArgs(_EntityNameArgs):
    """Args for ``ontology.get_entity``."""


class OntologyGetRelationshipsArgs(_EntityNameArgs):
    """Args for ``ontology.get_relationships``."""


class OntologySearchArgs(_ArgsBase):
    """Args for ``ontology.search``."""

    query: NotBlankStr = Field(description="Search query")
