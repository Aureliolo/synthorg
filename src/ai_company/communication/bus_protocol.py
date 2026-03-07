"""Message bus protocol (DESIGN_SPEC Section 5.4).

Defines the swappable interface for message bus backends.  The
default implementation is :class:`InMemoryMessageBus` in
``bus_memory.py``.
"""

from typing import Protocol, runtime_checkable

from ai_company.communication.channel import Channel  # noqa: TC001
from ai_company.communication.message import Message  # noqa: TC001
from ai_company.communication.subscription import (
    DeliveryEnvelope,  # noqa: TC001
    Subscription,  # noqa: TC001
)


@runtime_checkable
class MessageBus(Protocol):
    """Protocol for message bus backends.

    All implementations must support the full lifecycle (start/stop),
    channel management, pub/sub messaging, direct messaging, and
    channel history.

    Uses a **pull model**: consumers call :meth:`receive` to get the
    next message rather than registering push callbacks.
    """

    async def start(self) -> None:
        """Start the bus and create pre-configured channels.

        Raises:
            MessageBusAlreadyRunningError: If the bus is already running.
        """
        ...

    async def stop(self) -> None:
        """Stop the bus gracefully.  Idempotent."""
        ...

    @property
    def is_running(self) -> bool:
        """Whether the bus is currently running."""
        ...

    async def publish(self, message: Message) -> None:
        """Publish a message to its channel.

        The target channel is determined by ``message.channel``.

        Args:
            message: The message to publish.

        Raises:
            MessageBusNotRunningError: If the bus is not running.
            ChannelNotFoundError: If the target channel does not exist.
        """
        ...

    async def send_direct(
        self,
        message: Message,
        *,
        recipient: str,
    ) -> None:
        """Send a direct message between two agents.

        Lazily creates a DIRECT channel named ``@{a}:{b}`` (where
        a, b are the sorted agent IDs) and subscribes both agents.

        Args:
            message: The message to send (``message.sender`` is the
                sender).
            recipient: The recipient agent ID.

        Raises:
            MessageBusNotRunningError: If the bus is not running.
        """
        ...

    async def subscribe(
        self,
        channel_name: str,
        subscriber_id: str,
    ) -> Subscription:
        """Subscribe an agent to a channel.

        Idempotent — returns the existing subscription if already
        subscribed.

        Args:
            channel_name: Channel to subscribe to.
            subscriber_id: Agent ID of the subscriber.

        Returns:
            The subscription record.

        Raises:
            MessageBusNotRunningError: If the bus is not running.
            ChannelNotFoundError: If the channel does not exist.
        """
        ...

    async def unsubscribe(
        self,
        channel_name: str,
        subscriber_id: str,
    ) -> None:
        """Remove an agent's subscription from a channel.

        Args:
            channel_name: Channel to unsubscribe from.
            subscriber_id: Agent ID to remove.

        Raises:
            MessageBusNotRunningError: If the bus is not running.
            NotSubscribedError: If the agent is not subscribed.
        """
        ...

    async def receive(
        self,
        channel_name: str,
        subscriber_id: str,
        *,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> DeliveryEnvelope | None:
        """Receive the next message from a channel.

        Awaits until a message is available or the timeout expires.
        When ``timeout`` is ``None``, awaits indefinitely.

        Args:
            channel_name: Channel to receive from.
            subscriber_id: Agent ID receiving.
            timeout: Seconds to wait before returning ``None``.

        Returns:
            A delivery envelope, or ``None`` on timeout.
        """
        ...

    async def create_channel(self, channel: Channel) -> Channel:
        """Create a new channel.

        Args:
            channel: Channel definition to create.

        Returns:
            The created channel.

        Raises:
            MessageBusNotRunningError: If the bus is not running.
            ChannelAlreadyExistsError: If a channel with that name
                already exists.
        """
        ...

    async def get_channel(self, channel_name: str) -> Channel:
        """Get a channel by name.

        Args:
            channel_name: Name of the channel.

        Returns:
            The channel.

        Raises:
            ChannelNotFoundError: If the channel does not exist.
        """
        ...

    async def list_channels(self) -> tuple[Channel, ...]:
        """List all channels.

        Returns:
            All registered channels.
        """
        ...

    async def get_channel_history(
        self,
        channel_name: str,
        *,
        limit: int | None = None,
    ) -> tuple[Message, ...]:
        """Get message history for a channel.

        Args:
            channel_name: Channel to query.
            limit: Maximum number of most recent messages to return.

        Returns:
            Messages in chronological order.

        Raises:
            ChannelNotFoundError: If the channel does not exist.
        """
        ...
