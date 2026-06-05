"""Agent domain MCP handlers.

Shims the 18 agent tools onto the existing HR services
(``agent_registry`` / ``AgentRegistryService``, ``performance_tracker``,
``training_service``). The handler bodies live in sibling modules:
CRUD + observability in ``agents_crud``, personality + training in
``agents_training``, and autonomy + collaboration in ``agents_autonomy``.
This module aggregates them into the read-only ``AGENT_HANDLERS`` map.

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
from synthorg.meta.mcp.handlers.agents_autonomy import (
    collaboration_get_calibration as _collaboration_get_calibration,
)
from synthorg.meta.mcp.handlers.agents_autonomy import (
    collaboration_get_score as _collaboration_get_score,
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
from synthorg.meta.mcp.handlers.agents_training import (
    _personalities_get,
    _personalities_list,
    _training_get_session,
    _training_list_sessions,
    _training_start_session,
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
        "synthorg_training_list_sessions": _training_list_sessions,
        "synthorg_training_get_session": _training_get_session,
        "synthorg_training_start_session": _training_start_session,
        "synthorg_autonomy_get": _autonomy_get,
        "synthorg_autonomy_update": _autonomy_update,
        "synthorg_collaboration_get_score": _collaboration_get_score,
        "synthorg_collaboration_get_calibration": _collaboration_get_calibration,
    }
)
