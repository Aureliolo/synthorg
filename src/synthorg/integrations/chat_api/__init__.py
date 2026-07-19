"""Chat-platform Web API clients for the agent-facing chat tools.

Two-way chat surface (send / read / list channels / look up user) keyed
by :class:`ConnectionType`. Slack is the first platform; others slot in
by registering a client, so the tool surface stays vendor-neutral.
"""

from synthorg.integrations.chat_api.factory import (
    build_chat_api_client,
    chat_api_supported,
)
from synthorg.integrations.chat_api.protocol import (
    ChatApiClient,
    ChatChannel,
    ChatMessage,
    ChatMessageRef,
    ChatUser,
)

__all__ = [
    "ChatApiClient",
    "ChatChannel",
    "ChatMessage",
    "ChatMessageRef",
    "ChatUser",
    "build_chat_api_client",
    "chat_api_supported",
]
