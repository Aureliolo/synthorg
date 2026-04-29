"""Communication-domain MCP args.

Covers messages, meetings, connections, webhooks, tunnel.
"""

from pydantic import Field

from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic field type
from synthorg.meta.mcp.domains._common_args import (
    DestructiveGuardrailFields,
    PaginationFields,
    _ArgsBase,
)


class MessagesListArgs(PaginationFields):
    """Args for ``messages.list``."""

    channel: NotBlankStr | None = Field(default=None, description="Filter by channel")
    sender: NotBlankStr | None = Field(default=None, description="Filter by sender")


class MessagesGetArgs(_ArgsBase):
    """Args for ``messages.get``."""

    message_id: NotBlankStr = Field(description="Message UUID")


class MessagesSendArgs(_ArgsBase):
    """Args for ``messages.send``."""

    channel: NotBlankStr = Field(description="Target channel")
    content: NotBlankStr = Field(description="Message content")
    sender: NotBlankStr | None = Field(default=None, description="Sender name")


class MessagesDeleteArgs(DestructiveGuardrailFields):
    """Args for ``messages.delete`` (destructive)."""

    message_id: NotBlankStr = Field(description="Message UUID")


class MeetingsListArgs(PaginationFields):
    """Args for ``meetings.list``."""


class MeetingsGetArgs(_ArgsBase):
    """Args for ``meetings.get``."""

    meeting_id: NotBlankStr = Field(description="Meeting UUID")


class MeetingsCreateArgs(_ArgsBase):
    """Args for ``meetings.create``."""

    title: NotBlankStr = Field(description="Meeting title")
    participants: tuple[NotBlankStr, ...] = Field(
        default=(),
        description="Participant names",
    )


class MeetingsUpdateArgs(_ArgsBase):
    """Args for ``meetings.update``."""

    meeting_id: NotBlankStr = Field(description="Meeting UUID")
    updates: dict[str, object] = Field(description="Fields to update")


class MeetingsDeleteArgs(DestructiveGuardrailFields):
    """Args for ``meetings.delete`` (destructive)."""

    meeting_id: NotBlankStr = Field(description="Meeting UUID")


class ConnectionsListArgs(_ArgsBase):
    """Args for ``connections.list``: no fields."""


class ConnectionsGetArgs(_ArgsBase):
    """Args for ``connections.get``."""

    name: NotBlankStr = Field(description="Connection name")


class ConnectionsCreateArgs(_ArgsBase):
    """Args for ``connections.create``."""

    name: NotBlankStr = Field(description="Connection name")
    connection_type: NotBlankStr = Field(description="Connection type")
    credentials: dict[str, object] = Field(
        default_factory=dict,
        description="Connection credentials",
    )


class ConnectionsDeleteArgs(DestructiveGuardrailFields):
    """Args for ``connections.delete``.

    Destructive admin op: callers must supply ``confirm=True`` and a
    non-blank ``reason`` (mixin), in addition to the connection name.
    """

    name: NotBlankStr = Field(description="Connection name")


class ConnectionsCheckHealthArgs(_ArgsBase):
    """Args for ``connections.check_health``."""

    name: NotBlankStr = Field(description="Connection name")


class WebhooksListArgs(PaginationFields):
    """Args for ``webhooks.list``."""


class WebhooksGetArgs(_ArgsBase):
    """Args for ``webhooks.get``."""

    webhook_id: NotBlankStr = Field(description="Webhook UUID")


class WebhooksCreateArgs(_ArgsBase):
    """Args for ``webhooks.create``."""

    url: NotBlankStr = Field(description="Webhook URL")
    events: tuple[NotBlankStr, ...] = Field(
        min_length=1,
        description="Event types to subscribe",
    )


class WebhooksUpdateArgs(_ArgsBase):
    """Args for ``webhooks.update``."""

    webhook_id: NotBlankStr = Field(description="Webhook UUID")
    updates: dict[str, object] = Field(description="Fields to update")


class WebhooksDeleteArgs(DestructiveGuardrailFields):
    """Args for ``webhooks.delete``.

    Destructive admin op: callers must supply ``confirm=True`` and a
    non-blank ``reason`` (mixin), in addition to the webhook UUID.
    """

    webhook_id: NotBlankStr = Field(description="Webhook UUID")


class TunnelGetStatusArgs(_ArgsBase):
    """Args for ``tunnel.get_status``: no fields."""


class TunnelConnectArgs(_ArgsBase):
    """Args for ``tunnel.connect``."""

    target: NotBlankStr = Field(description="Tunnel target endpoint")
