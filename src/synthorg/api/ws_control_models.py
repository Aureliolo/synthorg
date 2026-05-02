"""Typed WebSocket control-plane message contract.

The four inbound (client -> server) message kinds share an ``action``
discriminator literal so :data:`WsControlMessage` deserialises into the
correct variant, eliminating the legacy field-pluck pattern in
:mod:`synthorg.api.controllers.ws_protocol`.

The shape mirrors the typed contract in
``web/src/api/types/websocket.ts`` (PR #1718). Wire-protocol
versioning lives on the outbound :class:`~synthorg.api.ws_models.WsEvent`
side; inbound control messages do not currently carry a version field.
"""

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Discriminator,
    Field,
    TypeAdapter,
)

from synthorg.core.types import NotBlankStr  # noqa: TC001 -- Pydantic field type


class WsAuthMessage(BaseModel):
    """First-message auth handshake carrying a one-time ticket.

    Attributes:
        action: Discriminator literal (always ``"auth"``).
        ticket: Opaque ticket string previously obtained from
            ``POST /api/v1/auth/ws-ticket``.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    action: Literal["auth"] = "auth"
    ticket: NotBlankStr


class WsSubscribeMessage(BaseModel):
    """Subscribe to a list of channels with optional filter overrides.

    Filter semantics mirror the legacy dict-based contract:
    ``filters=None`` (key absent) leaves existing filters untouched,
    ``filters={}`` clears them, ``filters={...}`` replaces them.

    Attributes:
        action: Discriminator literal (always ``"subscribe"``).
        channels: Channel identifiers to add to the active set.
        filters: Optional ``str -> str`` filter map. ``None`` means
            "leave existing filters unchanged"; an empty dict clears
            them.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    action: Literal["subscribe"] = "subscribe"
    channels: tuple[str, ...] = Field(default=())
    filters: dict[str, str] | None = None


class WsUnsubscribeMessage(BaseModel):
    """Unsubscribe from a list of channels.

    Attributes:
        action: Discriminator literal (always ``"unsubscribe"``).
        channels: Channel identifiers to remove from the active set.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    action: Literal["unsubscribe"] = "unsubscribe"
    channels: tuple[str, ...] = Field(default=())


class WsPingMessage(BaseModel):
    """Heartbeat ping; the server replies with ``{"action": "pong"}``.

    Attributes:
        action: Discriminator literal (always ``"ping"``).
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    action: Literal["ping"] = "ping"


WsControlMessage = Annotated[
    WsAuthMessage | WsSubscribeMessage | WsUnsubscribeMessage | WsPingMessage,
    Discriminator("action"),
]
"""Discriminated union of inbound WebSocket control messages.

Pydantic uses the ``action`` literal on each variant to deserialise
into the correct typed variant. The auth handshake variant is part of
the same union so the pre-handshake first-message validation can route
through the same boundary helper as post-handshake control frames.
"""


WS_CONTROL_MESSAGE_ADAPTER: TypeAdapter[WsControlMessage] = TypeAdapter(
    WsControlMessage,
)


__all__ = [
    "WS_CONTROL_MESSAGE_ADAPTER",
    "WsAuthMessage",
    "WsControlMessage",
    "WsPingMessage",
    "WsSubscribeMessage",
    "WsUnsubscribeMessage",
]
