"""Subscription and delivery envelope models (see Communication design page)."""

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

    model_config = ConfigDict(frozen=True)

    channel_name: NotBlankStr = Field(description="Channel name")
    subscriber_id: NotBlankStr = Field(description="Subscriber agent ID")
    subscribed_at: AwareDatetime = Field(description="When subscribed")


class DeliveryEnvelope(BaseModel):
    """Wraps a message with delivery metadata.

    Tells the subscriber which channel a message arrived through
    and when it was delivered.

    Attributes:
        message: The delivered message.
        channel_name: Channel the message was delivered through.
        delivered_at: When the message was delivered to this subscriber.
    """

    model_config = ConfigDict(frozen=True)

    message: Message = Field(description="The delivered message")
    channel_name: NotBlankStr = Field(description="Delivery channel")
    delivered_at: AwareDatetime = Field(description="When delivered")
