"""Integrations facades for the MCP handler layer.

One facade per primitive: catalog, OAuth, clients, artifacts, ontology.
Each wraps the corresponding primitive on AppState (or an in-memory
store when no primitive yet exists) and raises
:class:`CapabilityNotSupportedError` for methods the primitive does
not yet implement.
"""

from synthorg.integrations.mcp_facades._artifacts import ArtifactFacadeService
from synthorg.integrations.mcp_facades._catalog import MCPCatalogFacadeService
from synthorg.integrations.mcp_facades._clients import ClientFacadeService
from synthorg.integrations.mcp_facades._oauth import OAuthFacadeService
from synthorg.integrations.mcp_facades._ontology import OntologyFacadeService

__all__ = [
    "ArtifactFacadeService",
    "ClientFacadeService",
    "MCPCatalogFacadeService",
    "OAuthFacadeService",
    "OntologyFacadeService",
]
