"""Communication domain MCP handlers (per-sub-domain package).

21 tools spanning messages, meetings, connections, webhooks, and the
sandbox tunnel -- split one module per sub-domain. Each sub-module
exports its ``<DOMAIN>_HANDLERS`` map; ``COMMUNICATION_HANDLERS``
aggregates them for the communication feature's deferred loader and the
MCP dispatch table.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import TYPE_CHECKING

from synthorg.meta.mcp.handler_protocol import ToolHandler
from synthorg.meta.mcp.handlers.communication.connections import CONNECTIONS_HANDLERS
from synthorg.meta.mcp.handlers.communication.meetings import MEETINGS_HANDLERS
from synthorg.meta.mcp.handlers.communication.messages import MESSAGES_HANDLERS
from synthorg.meta.mcp.handlers.communication.tunnel import TUNNEL_HANDLERS
from synthorg.meta.mcp.handlers.communication.webhooks import WEBHOOKS_HANDLERS

COMMUNICATION_HANDLERS: Mapping[str, ToolHandler] = MappingProxyType(
    {
        **MESSAGES_HANDLERS,
        **MEETINGS_HANDLERS,
        **CONNECTIONS_HANDLERS,
        **WEBHOOKS_HANDLERS,
        **TUNNEL_HANDLERS,
    },
)
