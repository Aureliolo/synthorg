"""In-memory message bus implementation (DESIGN_SPEC Section 5.4).

Default backend using asyncio primitives.  Suitable for single-process
deployments and testing.
"""

import asyncio
from collections import deque
from datetime import UTC, datetime
from typing import NoReturn

from ai_company.communication.channel import Channel
from ai_company.communication.config import MessageBusConfig  # noqa: TC001
from ai_company.communication.enums import ChannelType
from ai_company.communication.errors import (
    ChannelAlreadyExistsError,
    ChannelNotFoundError,
    MessageBusAlreadyRunningError,
    MessageBusNotRunningError,
    NotSubscribedError,
)
from ai_company.communication.message import Message  # noqa: TC001
from ai_company.communication.subscription import (
    DeliveryEnvelope,
    Subscription,
)
from ai_company.observability import get_logger
from ai_company.observability.events.communication import (
    COMM_BUS_STARTED,
    COMM_BUS_STOPPED,
    COMM_CHANNEL_ALREADY_EXISTS,
    COMM_CHANNEL_CREATED,
    COMM_CHANNEL_NOT_FOUND,
    COMM_DIRECT_SENT,
    COMM_HISTORY_QUERIED,
    COMM_MESSAGE_DELIVERED,
    COMM_MESSAGE_PUBLISHED,
    COMM_SUBSCRIPTION_CREATED,
    COMM_SUBSCRIPTION_NOT_FOUND,
    COMM_SUBSCRIPTION_REMOVED,
)

logger = get_logger(__name__)


def _raise_channel_not_found(channel_name: str) -> NoReturn:
    """Log and raise :class:`ChannelNotFoundError`."""
    logger.warning(COMM_CHANNEL_NOT_FOUND, channel=channel_name)
    msg = f"Channel not found: {channel_name}"
    raise ChannelNotFoundError(msg, context={"channel": channel_name})


def _raise_not_subscribed(
    channel_name: str,
    subscriber_id: str,
) -> NoReturn:
    """Log and raise :class:`NotSubscribedError`."""
    logger.warning(
        COMM_SUBSCRIPTION_NOT_FOUND,
        channel=channel_name,
        subscriber=subscriber_id,
    )
    msg = f"Not subscribed to {channel_name}"
    raise NotSubscribedError(
        msg,
        context={
            "channel": channel_name,
            "subscriber": subscriber_id,
        },
    )


class InMemoryMessageBus:
    """In-memory message bus using asyncio queues.

    Args:
        config: Message bus configuration including pre-defined
            channels and retention settings.
    """

    def __init__(self, *, config: MessageBusConfig) -> None:
        self._config = config
        self._lock = asyncio.Lock()
        self._channels: dict[str, Channel] = {}
        self._queues: dict[tuple[str, str], asyncio.Queue[DeliveryEnvelope]] = {}
        self._history: dict[str, deque[Message]] = {}
        self._known_agents: set[str] = set()
        self._running = False

    @property
    def is_running(self) -> bool:
        """Whether the bus is currently running."""
        return self._running

    async def start(self) -> None:
        """Start the bus and create pre-configured channels.

        Raises:
            MessageBusAlreadyRunningError: If already running.
        """
        async with self._lock:
            if self._running:
                msg = "Message bus is already running"
                logger.warning(
                    COMM_BUS_STARTED,
                    status="already_running",
                )
                raise MessageBusAlreadyRunningError(msg)
            self._running = True
            maxlen = self._config.retention.max_messages_per_channel
            for name in self._config.channels:
                ch = Channel(name=name, type=ChannelType.TOPIC)
                self._channels[name] = ch
                self._history[name] = deque(maxlen=maxlen)
        logger.info(
            COMM_BUS_STARTED,
            channels_created=len(self._config.channels),
        )

    async def stop(self) -> None:
        """Stop the bus gracefully.  Idempotent."""
        async with self._lock:
            if not self._running:
                return
            self._running = False
        logger.info(COMM_BUS_STOPPED)

    def _require_running(self) -> None:
        """Raise if the bus is not running."""
        if not self._running:
            msg = "Message bus is not running"
            raise MessageBusNotRunningError(msg)

    def _ensure_queue(
        self,
        channel_name: str,
        subscriber_id: str,
    ) -> asyncio.Queue[DeliveryEnvelope]:
        """Get or create a per-(channel, subscriber) queue."""
        key = (channel_name, subscriber_id)
        if key not in self._queues:
            self._queues[key] = asyncio.Queue()
        return self._queues[key]

    async def publish(self, message: Message) -> None:
        """Publish a message to its channel.

        Args:
            message: The message to publish.

        Raises:
            MessageBusNotRunningError: If not running.
            ChannelNotFoundError: If the channel does not exist.
        """
        async with self._lock:
            self._require_running()
            channel_name = message.channel
            if channel_name not in self._channels:
                _raise_channel_not_found(channel_name)
            channel = self._channels[channel_name]
            self._history[channel_name].append(message)
            now = datetime.now(UTC)
            if channel.type == ChannelType.BROADCAST:
                targets = self._known_agents
            else:
                targets = set(channel.subscribers)
            for sub_id in targets:
                queue = self._ensure_queue(channel_name, sub_id)
                envelope = DeliveryEnvelope(
                    message=message,
                    channel_name=channel_name,
                    delivered_at=now,
                )
                queue.put_nowait(envelope)
                logger.debug(
                    COMM_MESSAGE_DELIVERED,
                    channel=channel_name,
                    subscriber=sub_id,
                    message_id=str(message.id),
                )
        logger.info(
            COMM_MESSAGE_PUBLISHED,
            channel=channel_name,
            message_id=str(message.id),
            type=str(message.type),
        )

    async def send_direct(
        self,
        message: Message,
        *,
        recipient: str,
    ) -> None:
        """Send a direct message between two agents.

        Lazily creates a DIRECT channel named ``@{sorted_pair}`` and
        subscribes both agents.

        Args:
            message: The message to send.
            recipient: The recipient agent ID.

        Raises:
            MessageBusNotRunningError: If not running.
        """
        sender = message.sender
        pair = sorted([sender, recipient])
        channel_name = f"@{pair[0]}:{pair[1]}"
        async with self._lock:
            self._require_running()
            if channel_name not in self._channels:
                ch = Channel(
                    name=channel_name,
                    type=ChannelType.DIRECT,
                    subscribers=(pair[0], pair[1]),
                )
                self._channels[channel_name] = ch
                maxlen = self._config.retention.max_messages_per_channel
                self._history[channel_name] = deque(maxlen=maxlen)
                logger.info(
                    COMM_CHANNEL_CREATED,
                    channel=channel_name,
                    type=str(ChannelType.DIRECT),
                )
            for agent_id in pair:
                self._known_agents.add(agent_id)
                self._ensure_queue(channel_name, agent_id)
            current_ch = self._channels[channel_name]
            current_subs = set(current_ch.subscribers)
            needed = {pair[0], pair[1]}
            if not needed.issubset(current_subs):
                new_subs = tuple(sorted(current_subs | needed))
                self._channels[channel_name] = current_ch.model_copy(
                    update={"subscribers": new_subs},
                )
            self._history[channel_name].append(message)
            now = datetime.now(UTC)
            for agent_id in pair:
                envelope = DeliveryEnvelope(
                    message=message,
                    channel_name=channel_name,
                    delivered_at=now,
                )
                self._queues[(channel_name, agent_id)].put_nowait(
                    envelope,
                )
                logger.debug(
                    COMM_MESSAGE_DELIVERED,
                    channel=channel_name,
                    subscriber=agent_id,
                    message_id=str(message.id),
                )
        logger.info(
            COMM_DIRECT_SENT,
            channel=channel_name,
            sender=sender,
            recipient=recipient,
            message_id=str(message.id),
        )

    async def subscribe(
        self,
        channel_name: str,
        subscriber_id: str,
    ) -> Subscription:
        """Subscribe an agent to a channel.

        Args:
            channel_name: Channel to subscribe to.
            subscriber_id: Agent ID of the subscriber.

        Returns:
            The subscription record.

        Raises:
            MessageBusNotRunningError: If not running.
            ChannelNotFoundError: If the channel does not exist.
        """
        async with self._lock:
            self._require_running()
            if channel_name not in self._channels:
                _raise_channel_not_found(channel_name)
            self._known_agents.add(subscriber_id)
            channel = self._channels[channel_name]
            if subscriber_id in channel.subscribers:
                return Subscription(
                    channel_name=channel_name,
                    subscriber_id=subscriber_id,
                    subscribed_at=datetime.now(UTC),
                )
            new_subs = (*channel.subscribers, subscriber_id)
            self._channels[channel_name] = channel.model_copy(
                update={"subscribers": new_subs},
            )
            self._ensure_queue(channel_name, subscriber_id)
        now = datetime.now(UTC)
        logger.info(
            COMM_SUBSCRIPTION_CREATED,
            channel=channel_name,
            subscriber=subscriber_id,
        )
        return Subscription(
            channel_name=channel_name,
            subscriber_id=subscriber_id,
            subscribed_at=now,
        )

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
            NotSubscribedError: If the agent is not subscribed.
        """
        async with self._lock:
            if channel_name not in self._channels:
                _raise_not_subscribed(channel_name, subscriber_id)
            channel = self._channels[channel_name]
            if subscriber_id not in channel.subscribers:
                _raise_not_subscribed(channel_name, subscriber_id)
            new_subs = tuple(s for s in channel.subscribers if s != subscriber_id)
            self._channels[channel_name] = channel.model_copy(
                update={"subscribers": new_subs},
            )
            self._queues.pop((channel_name, subscriber_id), None)
        logger.info(
            COMM_SUBSCRIPTION_REMOVED,
            channel=channel_name,
            subscriber=subscriber_id,
        )

    async def receive(
        self,
        channel_name: str,
        subscriber_id: str,
        *,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> DeliveryEnvelope | None:
        """Receive the next message from a channel.

        Args:
            channel_name: Channel to receive from.
            subscriber_id: Agent ID receiving.
            timeout: Seconds to wait before returning ``None``.

        Returns:
            A delivery envelope, or ``None`` on timeout.
        """
        async with self._lock:
            queue = self._ensure_queue(channel_name, subscriber_id)
        try:
            if timeout is not None:
                return await asyncio.wait_for(
                    queue.get(),
                    timeout=timeout,
                )
            return await queue.get()
        except TimeoutError:
            return None

    async def create_channel(self, channel: Channel) -> Channel:
        """Create a new channel.

        Args:
            channel: Channel definition to create.

        Returns:
            The created channel.

        Raises:
            MessageBusNotRunningError: If not running.
            ChannelAlreadyExistsError: If already exists.
        """
        async with self._lock:
            self._require_running()
            if channel.name in self._channels:
                logger.warning(
                    COMM_CHANNEL_ALREADY_EXISTS,
                    channel=channel.name,
                )
                msg = f"Channel already exists: {channel.name}"
                raise ChannelAlreadyExistsError(
                    msg,
                    context={"channel": channel.name},
                )
            self._channels[channel.name] = channel
            maxlen = self._config.retention.max_messages_per_channel
            self._history[channel.name] = deque(maxlen=maxlen)
        logger.info(
            COMM_CHANNEL_CREATED,
            channel=channel.name,
            type=str(channel.type),
        )
        return channel

    async def get_channel(self, channel_name: str) -> Channel:
        """Get a channel by name.

        Args:
            channel_name: Name of the channel.

        Returns:
            The channel.

        Raises:
            ChannelNotFoundError: If the channel does not exist.
        """
        async with self._lock:
            if channel_name not in self._channels:
                _raise_channel_not_found(channel_name)
            return self._channels[channel_name]

    async def list_channels(self) -> tuple[Channel, ...]:
        """List all channels.

        Returns:
            All registered channels.
        """
        async with self._lock:
            return tuple(self._channels.values())

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
        async with self._lock:
            if channel_name not in self._channels:
                _raise_channel_not_found(channel_name)
            messages = list(
                self._history.get(channel_name, deque()),
            )
        if limit is not None and limit < len(messages):
            messages = messages[-limit:]
        logger.debug(
            COMM_HISTORY_QUERIED,
            channel=channel_name,
            count=len(messages),
            limit=limit,
        )
        return tuple(messages)
