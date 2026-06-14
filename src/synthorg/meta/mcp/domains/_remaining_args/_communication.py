"""Communication-domain MCP args.

Covers messages, meetings, connections, webhooks, tunnel.
"""

from pydantic import Field

from synthorg.core.types import NotBlankStr
from synthorg.meta.mcp.domains._common_args import (
    AdminGuardrailFields,
    PaginationFields,
    _ArgsBase,
)


class MessagesListArgs(PaginationFields):
    """Args for ``messages.list``."""

    channel: NotBlankStr | None = Field(default=None, description="Filter by channel")
    sender: NotBlankStr | None = Field(default=None, description="Filter by sender")


class MessagesGetArgs(_ArgsBase):
    """Args for ``messages.get``."""

    channel: NotBlankStr = Field(description="Channel containing the message")
    message_id: NotBlankStr = Field(description="Message UUID")


class MessagesSendArgs(_ArgsBase):
    """Args for ``messages.send``.

    ``message`` is the full :class:`Message` payload, validated by the
    handler against that model; it is a polymorphic ``dict[str, object]``
    here because its closed shape lives in ``synthorg.communication.message``.
    """

    message: dict[str, object] = Field(description="Message payload")


class MessagesDeleteArgs(AdminGuardrailFields):
    """Args for ``messages.delete`` (destructive)."""

    message_id: NotBlankStr = Field(description="Message UUID")


class MeetingsListArgs(PaginationFields):
    """Args for ``meetings.list``."""

    status: NotBlankStr | None = Field(default=None, description="Filter by status")
    meeting_type: NotBlankStr | None = Field(
        default=None,
        description="Filter by meeting type",
    )


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


class MeetingsDeleteArgs(AdminGuardrailFields):
    """Args for ``meetings.delete`` (destructive)."""

    meeting_id: NotBlankStr = Field(description="Meeting UUID")


class ConnectionsListArgs(PaginationFields):
    """Args for ``connections.list``."""


class ConnectionsGetArgs(_ArgsBase):
    """Args for ``connections.get``."""

    name: NotBlankStr = Field(description="Connection name")


class ConnectionsCreateArgs(AdminGuardrailFields):
    """Args for ``connections.create`` (admin op).

    Admin op: callers must supply ``confirm=True`` and a non-blank
    ``reason`` (mixin) in addition to the connection metadata.
    """

    name: NotBlankStr = Field(description="Connection name")
    connection_type: NotBlankStr = Field(description="Connection type")
    auth_method: NotBlankStr = Field(description="Authentication method")
    credentials: dict[str, str] = Field(
        default_factory=dict,
        description="Connection credentials",
    )
    base_url: NotBlankStr | None = Field(default=None, description="Base URL")
    metadata: dict[str, str] | None = Field(
        default=None,
        description="Free-form connection metadata",
    )


class ConnectionsDeleteArgs(AdminGuardrailFields):
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


class WebhooksCreateArgs(AdminGuardrailFields):
    """Args for ``webhooks.create`` (admin op).

    ``definition`` is the full :class:`WebhookDefinition` payload,
    validated by the handler against that model; it is a polymorphic
    ``dict[str, object]`` because its closed shape lives in
    ``synthorg.integrations.webhooks.models``.
    """

    definition: dict[str, object] = Field(description="WebhookDefinition payload")


class WebhooksUpdateArgs(AdminGuardrailFields):
    """Args for ``webhooks.update`` (admin op).

    ``definition`` is the full :class:`WebhookDefinition` payload
    (including ``id``), validated by the handler against that model.
    """

    definition: dict[str, object] = Field(description="WebhookDefinition payload")


class WebhooksDeleteArgs(AdminGuardrailFields):
    """Args for ``webhooks.delete``.

    Destructive admin op: callers must supply ``confirm=True`` and a
    non-blank ``reason`` (mixin), in addition to the webhook UUID.
    """

    webhook_id: NotBlankStr = Field(description="Webhook UUID")


class TunnelGetStatusArgs(_ArgsBase):
    """Args for ``tunnel.get_status``: no fields."""


class TunnelConnectArgs(AdminGuardrailFields):
    """Args for ``tunnel.connect`` (admin op).

    Parameterless reconnect: callers supply only the guardrail fields
    (``confirm=True`` + non-blank ``reason``) since the underlying
    tunnel service does not accept a target endpoint at this layer.
    """
