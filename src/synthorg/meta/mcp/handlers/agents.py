"""Agent domain MCP handlers.

Shims the 13 agent tools onto the existing HR services
(``agent_registry`` / ``AgentRegistryService``, ``performance_tracker``).
The handler bodies live in sibling modules: CRUD + observability in
``agents_crud``, personality in ``agents_personalities``, and autonomy in
``agents_autonomy``. This module aggregates them into the read-only
``AGENT_HANDLERS`` map.

Destructive ops
---------------
``synthorg_agents_delete`` enforces the full
``confirm=True`` + non-blank ``reason`` + non-``None`` ``actor`` guardrail
and emits ``MCP_ADMIN_OP_EXECUTED`` on success.
"""

from collections.abc import Mapping
from types import MappingProxyType

from synthorg.meta.mcp.handler_protocol import ToolHandler
from synthorg.meta.mcp.handlers.agents_autonomy import (
    autonomy_get as _autonomy_get,
)
from synthorg.meta.mcp.handlers.agents_autonomy import (
    autonomy_update as _autonomy_update,
)
from synthorg.meta.mcp.handlers.agents_crud import (
    _agents_create,
    _agents_delete,
    _agents_get,
    _agents_get_activity,
    _agents_get_health,
    _agents_get_history,
    _agents_get_performance,
    _agents_list,
    _agents_update,
)
from synthorg.meta.mcp.handlers.agents_personalities import (
    _personalities_get,
    _personalities_list,
)

AGENT_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        "synthorg_agents_list": _agents_list,
        "synthorg_agents_get": _agents_get,
        "synthorg_agents_create": _agents_create,
        "synthorg_agents_update": _agents_update,
        "synthorg_agents_delete": _agents_delete,
        "synthorg_agents_get_performance": _agents_get_performance,
        "synthorg_agents_get_activity": _agents_get_activity,
        "synthorg_agents_get_history": _agents_get_history,
        "synthorg_agents_get_health": _agents_get_health,
        "synthorg_personalities_list": _personalities_list,
        "synthorg_personalities_get": _personalities_get,
        "synthorg_autonomy_get": _autonomy_get,
        "synthorg_autonomy_update": _autonomy_update,
    }
)
