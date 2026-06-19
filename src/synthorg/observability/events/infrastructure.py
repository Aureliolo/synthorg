"""Infrastructure event constants.

MCP-audit events for infrastructure-layer facades (projects, client
request ledger).  Other infrastructure subsystems (backup, settings,
providers, users, audit, health) have dedicated event modules.
"""

from typing import Final

# -- Facade capability guards -------------------------------------------

INFRA_CAPABILITY_UNSUPPORTED: Final[str] = "infrastructure.capability.unsupported"

# -- Project lifecycle MCP audit events ---------------------------------

PROJECT_CREATED_VIA_MCP: Final[str] = "infrastructure.project.created_via_mcp"
PROJECT_UPDATED_VIA_MCP: Final[str] = "infrastructure.project.updated_via_mcp"
PROJECT_DELETED_VIA_MCP: Final[str] = "infrastructure.project.deleted_via_mcp"

# -- Request ledger MCP audit events ------------------------------------

REQUEST_CREATED_VIA_MCP: Final[str] = "infrastructure.request.created_via_mcp"
