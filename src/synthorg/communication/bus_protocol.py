"""Message bus protocol (see Communication design page).

Defines the swappable interface for message bus backends.  The
default implementation is :class:`InMemoryMessageBus` in
``synthorg.communication.bus.memory``.
"""

from collections.abc import Sequence  # noqa: TC003
from typing import Protocol, runtime_checkable

from synthorg.communication.channel import Channel  # noqa: TC001
from synthorg.communication.message import Message  # noqa: TC001
from synthorg.communication.subscription import (
    DeliveryEnvelope,  # noqa: TC001
    Subscription,  # noqa: TC001
)
from synthorg.core.types import NotBlankStr  # noqa: TC001


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
        """Whether the bus client thinks it is running.

        Local state check: flipped by :meth:`start` / :meth:`stop`.
        Does not reflect live server reachability -- use
        :meth:`health_check` for that.
        """
        ...

    async def health_check(self) -> bool:
        """Live liveness probe against the underlying transport.

        Unlike :attr:`is_running`, this performs an actual
        round-trip to confirm the broker is reachable and the
        connection is healthy. Implementations return ``False``
        rather than raising when the probe fails, so the caller
        can treat the bus as degraded without a try/except.
        """
        ...

    async def publish(
        self,
        message: Message,
        *,
        ttl_seconds: float | None = None,
    ) -> None:
        """Publish a message to its channel.

        The target channel is determined by ``message.channel``.

        Args:
            message: The message to publish.
            ttl_seconds: Optional per-message TTL in seconds.  When set,
                the message expires after this duration on the server.
                Requires the NATS stream to have ``allow_msg_ttl=True``.
                ``None`` (default) defers to stream retention policy.

        Raises:
            MessageBusNotRunningError: If the bus is not running.
            ChannelNotFoundError: If the target channel does not exist.
        """
        ...

    async def send_direct(
        self,
        message: Message,
        *,
        recipient: NotBlankStr,
        ttl_seconds: float | None = None,
    ) -> None:
        """Send a direct message between two agents.

        Lazily creates a DIRECT channel named ``@{a}:{b}`` (where
        a, b are the sorted agent IDs) and subscribes both agents.

        Args:
            message: The message to send (``message.sender`` is the
                sender).
            recipient: The recipient agent ID.
            ttl_seconds: Optional per-message TTL in seconds.

        Raises:
            MessageBusNotRunningError: If the bus is not running.
            ValueError: If the recipient does not match
                ``message.to``, an agent ID contains the reserved
                separator, or the message exceeds the payload limit.
        """
        ...

    async def subscribe(
        self,
        channel_name: NotBlankStr,
        subscriber_id: NotBlankStr,
    ) -> Subscription:
        """Subscribe an agent to a channel.

        Idempotent -- returns a fresh subscription record if already
        subscribed (the channel's subscriber list is not duplicated).

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
        channel_name: NotBlankStr,
        subscriber_id: NotBlankStr,
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
        channel_name: NotBlankStr,
        subscriber_id: NotBlankStr,
        *,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> DeliveryEnvelope | None:
        """Receive the next message from a channel.

        Awaits until a message is available, the timeout expires, or
        the bus is stopped.  When ``timeout`` is ``None``, awaits
        indefinitely (or until shutdown).

        Args:
            channel_name: Channel to receive from.
            subscriber_id: Agent ID receiving.
            timeout: Seconds to wait before returning ``None``.

        Returns:
            A delivery envelope, or ``None`` on timeout or shutdown.

        Raises:
            MessageBusNotRunningError: If the bus is not running.
            ChannelNotFoundError: If the channel does not exist.
            NotSubscribedError: If the subscriber is not subscribed
                (for TOPIC and DIRECT channels).
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

    async def get_channel(self, channel_name: NotBlankStr) -> Channel:
        """Get a channel by name.

        Args:
            channel_name: Name of the channel.

        Returns:
            The channel.

        Raises:
            ChannelNotFoundError: If the channel does not exist.
        """
        ...

    async def publish_batch(
        self,
        messages: Sequence[Message],
        *,
        ttl_seconds: float | None = None,
    ) -> None:
        """Publish multiple messages in a batch.

        Optimised for throughput.  In NATS backends, uses pipelined
        async publishes for reduced round-trip overhead.

        Args:
            messages: Messages to publish.  An empty sequence is a
                no-op.
            ttl_seconds: Optional per-message TTL applied to every
                message in the batch.

        Raises:
            MessageBusNotRunningError: If the bus is not running.
            ChannelNotFoundError: If any target channel does not exist.
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
        channel_name: NotBlankStr,
        *,
        limit: int | None = None,
    ) -> tuple[Message, ...]:
        """Get message history for a channel.

        Args:
            channel_name: Channel to query.
            limit: Maximum number of most recent messages to return.
                Values ``<= 0`` return an empty tuple.

        Returns:
            Messages in chronological order.

        Raises:
            ChannelNotFoundError: If the channel does not exist.
        """
        ...
