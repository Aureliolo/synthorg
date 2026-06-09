"""Infrastructure domain MCP handlers (per-sub-domain package).

40 tools spanning health, settings, providers, backup, audit, events,
users, projects, requests, setup, simulations, template packs, and
integration health -- split one module per sub-domain. Each sub-module
exports its ``<DOMAIN>_HANDLERS`` map; ``INFRASTRUCTURE_HANDLERS``
aggregates them for the facades feature's deferred loader and the MCP
dispatch table.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING

from synthorg.meta.mcp.handler_protocol import ToolHandler
from synthorg.meta.mcp.handlers.infrastructure.audit_events import (
    AUDIT_EVENTS_HANDLERS,
)
from synthorg.meta.mcp.handlers.infrastructure.backup import BACKUP_HANDLERS
from synthorg.meta.mcp.handlers.infrastructure.health import HEALTH_HANDLERS
from synthorg.meta.mcp.handlers.infrastructure.integration_health import (
    INTEGRATION_HEALTH_HANDLERS,
)
from synthorg.meta.mcp.handlers.infrastructure.projects import PROJECTS_HANDLERS
from synthorg.meta.mcp.handlers.infrastructure.providers import PROVIDERS_HANDLERS
from synthorg.meta.mcp.handlers.infrastructure.requests import REQUESTS_HANDLERS
from synthorg.meta.mcp.handlers.infrastructure.settings import SETTINGS_HANDLERS
from synthorg.meta.mcp.handlers.infrastructure.setup import SETUP_HANDLERS
from synthorg.meta.mcp.handlers.infrastructure.simulations import SIMULATIONS_HANDLERS
from synthorg.meta.mcp.handlers.infrastructure.template_packs import (
    TEMPLATE_PACKS_HANDLERS,
)
from synthorg.meta.mcp.handlers.infrastructure.users import USERS_HANDLERS

INFRASTRUCTURE_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        **HEALTH_HANDLERS,
        **SETTINGS_HANDLERS,
        **PROVIDERS_HANDLERS,
        **BACKUP_HANDLERS,
        **AUDIT_EVENTS_HANDLERS,
        **USERS_HANDLERS,
        **PROJECTS_HANDLERS,
        **REQUESTS_HANDLERS,
        **SETUP_HANDLERS,
        **SIMULATIONS_HANDLERS,
        **TEMPLATE_PACKS_HANDLERS,
        **INTEGRATION_HEALTH_HANDLERS,
    },
)
