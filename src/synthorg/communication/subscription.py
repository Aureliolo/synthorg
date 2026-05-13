"""Subscription and delivery envelope models (see Communication design page)."""

# Pydantic v2 resolves field annotations at runtime, so Callable /
# Awaitable cannot live behind TYPE_CHECKING.
from collections.abc import Awaitable, Callable  # noqa: TC003

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from synthorg.communication.message import Message  # noqa: TC001
from synthorg.core.types import NotBlankStr  # noqa: TC001


class Subscription(BaseModel):
    """Tracks when an agent subscribed to a channel.

    Attributes:
        channel_name: Name of the channel subscribed to.
        subscriber_id: Agent ID of the subscriber.
        subscribed_at: When the subscription was created.
    """

    model_config = ConfigDict(frozen=True, allow_inf_nan=False, extra="forbid")

    channel_name: NotBlankStr = Field(description="Channel name")
    subscriber_id: NotBlankStr = Field(description="Subscriber agent ID")
    subscribed_at: AwareDatetime = Field(description="When subscribed")


async def _noop_ack() -> None:
    """Default ack callback for backends that ack synchronously upstream."""
    return


class DeliveryEnvelope(BaseModel):
    """Wraps a message with delivery metadata.

    Tells the subscriber which channel a message arrived through
    and when it was delivered.

    The envelope additionally carries an ``ack`` callable that
    backends invoke after the subscriber has accepted delivery. The
    NATS backend uses this seam to defer JetStream acknowledgement
    until *after* the subscriber's local queue has accepted the
    envelope, so an ack-then-deliver-failure cannot drop the message.
    The in-memory backend ships a no-op callable because delivery is
    already synchronous and there is no upstream ack to defer.

    Attributes:
        message: The delivered message.
        channel_name: Channel the message was delivered through.
        delivered_at: When the message was delivered to this subscriber.
        ack: Async callable invoked by the subscriber after the
            envelope has been consumed locally. Defaults to a no-op
            so callers in tests / in-memory backends never need to
            care. Excluded from serialisation via ``Field(exclude=True)``.
    """

    model_config = ConfigDict(
        frozen=True,
        allow_inf_nan=False,
        extra="forbid",
        arbitrary_types_allowed=True,
    )

    message: Message = Field(description="The delivered message")
    channel_name: NotBlankStr = Field(description="Delivery channel")
    delivered_at: AwareDatetime = Field(description="When delivered")
    ack: Callable[[], Awaitable[None]] = Field(
        default=_noop_ack,
        exclude=True,
        repr=False,
        description="Deferred ack callback invoked after local delivery",
    )
