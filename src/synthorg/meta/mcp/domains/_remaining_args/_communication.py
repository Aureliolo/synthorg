"""Communication-domain MCP args.

Covers messages, meetings, connections, webhooks, tunnel.
"""

from typing import Self

from pydantic import Field, model_validator

from synthorg.communication.meeting.enums import MeetingStatus
from synthorg.core.types import NotBlankStr
from synthorg.integrations.connections.field_metadata import reject_inline_secret_fields
from synthorg.integrations.connections.models import ConnectionType
from synthorg.meta.mcp.domains._common_args import (
    AdminGuardrailFields,
    PaginationFields,
    _ArgsBase,
)


class MessagesListArgs(PaginationFields):
    """Args for ``messages.list``."""

    channel: NotBlankStr | None = Field(default=None, description="Filter by channel")


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

    status: MeetingStatus | None = Field(default=None, description="Filter by status")
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


class ConnectionsFieldMetadataArgs(_ArgsBase):
    """Args for ``connections.field_metadata``: no fields."""


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
        description="Non-secret credential fields (e.g. host, port, dialect)",
    )
    credential_handles: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Secret credential fields as out-of-band capture handles"
            " (field name -> handle); resolved in-process, never inline"
        ),
    )
    connection_draft_id: NotBlankStr | None = Field(
        default=None,
        description="Setup draft id binding the credential handles",
    )
    base_url: NotBlankStr | None = Field(default=None, description="Base URL")
    metadata: dict[str, str] | None = Field(
        default=None,
        description="Free-form connection metadata",
    )

    @model_validator(mode="after")
    def _validate_credentials(self) -> Self:
        """Enforce the credential-boundary invariants (parity with REST).

        Handles require a draft id, and a secret field must be captured out of
        band (never inline in ``credentials``). An unrecognised
        ``connection_type`` is left for the handler to reject, so the
        no-inline-secret check is skipped for it here.

        Returns:
            ``self`` when both invariants hold.

        Raises:
            ValueError: If handles lack a draft id, or a secret field is inline.
        """
        if self.credential_handles and self.connection_draft_id is None:
            msg = "connection_draft_id is required when credential_handles are supplied"
            raise ValueError(msg)
        try:
            connection_type = ConnectionType(self.connection_type)
        except ValueError:
            return self
        reject_inline_secret_fields(connection_type, self.credentials.keys())
        return self


class ConnectionsRequestSecretCaptureArgs(AdminGuardrailFields):
    """Args for ``connections.request_secret_capture`` (admin op).

    Raised by the operator console mid-setup to ask the operator for one secret
    field out of band: the dashboard renders a masked input for
    ``(connection_type, field_name)`` and posts the value straight to the
    capture endpoint under ``draft_id``, so the raw value never enters the chat
    turn. Carries no value; the field's kind and label come from the backend
    metadata registry, never the caller. Admin op: callers supply ``confirm=True``
    and a non-blank ``reason`` (mixin) so triggering a credential-entry prompt
    carries the same role + actor-audit controls as ``connections.create``.
    """

    connection_type: NotBlankStr = Field(description="Connection type being set up")
    field_name: NotBlankStr = Field(description="Secret field to capture out of band")
    draft_id: NotBlankStr = Field(
        description="Setup draft id the captured handle will bind to",
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
