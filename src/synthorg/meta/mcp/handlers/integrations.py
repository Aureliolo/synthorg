"""Integrations domain MCP handlers.

21 tools across MCP catalog, OAuth providers, external clients,
artifacts, and ontology.  Each handler shims through the corresponding
facade on :class:`AppState`; capability gaps raise typed
``not_supported`` via :class:`CapabilityNotSupportedError`.

The handler bodies live in sibling modules: MCP catalog in
``integrations_catalog``, OAuth providers + external clients in
``integrations_oauth_clients``, and artifacts + ontology in
``integrations_artifacts_ontology``; the shared argument / serialisation
helpers live in ``_integrations_helpers``. This module aggregates them
into the read-only ``INTEGRATION_HANDLERS`` map.
"""

from collections.abc import Mapping
from types import MappingProxyType

from synthorg.meta.mcp.handler_protocol import ToolHandler
from synthorg.meta.mcp.handlers.integrations_artifacts_ontology import (
    _artifacts_create,
    _artifacts_delete,
    _artifacts_get,
    _artifacts_list,
    _ontology_get_entity,
    _ontology_get_relationships,
    _ontology_list_entities,
    _ontology_search,
)
from synthorg.meta.mcp.handlers.integrations_catalog import (
    _mcp_catalog_get,
    _mcp_catalog_install,
    _mcp_catalog_list,
    _mcp_catalog_search,
    _mcp_catalog_uninstall,
)
from synthorg.meta.mcp.handlers.integrations_oauth_clients import (
    _clients_create,
    _clients_deactivate,
    _clients_get,
    _clients_get_satisfaction,
    _clients_list,
    _oauth_configure_provider,
    _oauth_list_providers,
    _oauth_remove_provider,
)

INTEGRATION_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_mcp_catalog_list": _mcp_catalog_list,
        "synthorg_mcp_catalog_search": _mcp_catalog_search,
        "synthorg_mcp_catalog_get": _mcp_catalog_get,
        "synthorg_mcp_catalog_install": _mcp_catalog_install,
        "synthorg_mcp_catalog_uninstall": _mcp_catalog_uninstall,
        "synthorg_oauth_list_providers": _oauth_list_providers,
        "synthorg_oauth_configure_provider": _oauth_configure_provider,
        "synthorg_oauth_remove_provider": _oauth_remove_provider,
        "synthorg_clients_list": _clients_list,
        "synthorg_clients_get": _clients_get,
        "synthorg_clients_create": _clients_create,
        "synthorg_clients_deactivate": _clients_deactivate,
        "synthorg_clients_get_satisfaction": _clients_get_satisfaction,
        "synthorg_artifacts_list": _artifacts_list,
        "synthorg_artifacts_get": _artifacts_get,
        "synthorg_artifacts_create": _artifacts_create,
        "synthorg_artifacts_delete": _artifacts_delete,
        "synthorg_ontology_list_entities": _ontology_list_entities,
        "synthorg_ontology_get_entity": _ontology_get_entity,
        "synthorg_ontology_get_relationships": _ontology_get_relationships,
        "synthorg_ontology_search": _ontology_search,
    },
)
