"""Chat Web API client factory keyed on the connection type.

Selects the per-platform client via a
:class:`~synthorg.core.registry.StrategyRegistry`. Slack is the first
platform; others slot in by registering a builder, so the agent-facing
chat tools stay vendor-neutral. The Slack builder pins egress to
``slack.com`` (rejecting any other host) so a mis-set base_url cannot
exfiltrate the bot token.
"""

import re
from typing import Final

from synthorg.core.registry import StrategyRegistry
from synthorg.integrations.chat_api.protocol import ChatApiClient
from synthorg.integrations.chat_api.slack import SlackChatClient
from synthorg.integrations.connections.models import ConnectionType
from synthorg.integrations.errors import ChatApiError

_DEFAULT_SLACK_BASE_URL: Final[str] = "https://slack.com"
_SLACK_ALLOWED_BASE_URL: Final[re.Pattern[str]] = re.compile(
    r"^https://([a-z0-9-]+\.)?slack\.com$",
)


def _slack_api_base(base_url: str) -> str:
    """Validate + normalise a Slack base URL to its ``/api`` root.

    Returns:
        The ``https://[<subdomain>.]slack.com/api`` base for the client.

    Raises:
        ChatApiError: When ``base_url`` is not a slack.com host (a
            non-Slack override would leak the bot token).
    """
    origin = base_url or _DEFAULT_SLACK_BASE_URL
    if not _SLACK_ALLOWED_BASE_URL.fullmatch(origin):
        msg = "Slack base_url must match https://[<subdomain>.]slack.com"
        raise ChatApiError(msg)
    return f"{origin}/api"


def _build_slack(base_url: str, token: str, timeout: float) -> ChatApiClient:
    return SlackChatClient(
        api_base_url=_slack_api_base(base_url),
        token=token,
        timeout=timeout,
    )


_REGISTRY: StrategyRegistry[ChatApiClient] = StrategyRegistry(
    {ConnectionType.SLACK: _build_slack},
    kind="chat_api_client",
)


def chat_api_supported(connection_type: ConnectionType) -> bool:
    """Return ``True`` if a chat Web API client is wired for the type."""
    return connection_type in _REGISTRY


def build_chat_api_client(
    *,
    connection_type: ConnectionType,
    base_url: str,
    token: str,
    timeout: float,
) -> ChatApiClient:
    """Build the per-platform chat Web API client.

    Args:
        connection_type: Selects the platform implementation.
        base_url: The connection's base URL (empty defaults to the
            platform default host).
        token: Resolved bot token (header auth only, never logged).
        timeout: Per-request timeout in seconds.

    Returns:
        A client satisfying :class:`ChatApiClient`.

    Raises:
        StrategyFactoryNotFoundError: ``connection_type`` has no wired
            chat client (check ``chat_api_supported`` first).
        ChatApiError: The base URL failed the platform host allowlist.
    """
    return _REGISTRY.build(connection_type, base_url, token, timeout)


__all__ = ["build_chat_api_client", "chat_api_supported"]
