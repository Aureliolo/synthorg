"""Inbound chat (Slack Socket-Mode): human replies re-enter agent tasks.

The outbound :mod:`synthorg.integrations.chat_api` surface lets an agent
send/read chat. This package is the missing return path: a long-running
Socket-Mode WebSocket that consumes ``app_mention`` / ``message`` (incl.
DMs) / ``reaction_added`` events and routes a human's threaded reply back
to the parked task that asked for it, so a conversation completes without
a human ever touching the dashboard.

Layering: :mod:`models` (vendor-neutral event), :mod:`decode` (pure
Socket-Mode frame -> event, no I/O), :mod:`socket_mode` (the aiohttp
WebSocket transport), :mod:`consumer` (the kill-switched long-running
loop), :mod:`router` (event -> approval resume). Human content is fenced
with ``wrap_untrusted(TAG_TASK_DATA, ...)`` before it can reach a prompt.
"""

from synthorg.integrations.chat_api.inbound.models import (
    InboundChatEvent,
    InboundEventKind,
)
from synthorg.integrations.chat_api.inbound.registry import InboundThreadRegistry

__all__ = ["InboundChatEvent", "InboundEventKind", "InboundThreadRegistry"]
