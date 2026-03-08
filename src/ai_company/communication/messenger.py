"""Per-agent messenger facade over the message bus (DESIGN_SPEC Section 5.4)."""

from datetime import UTC, datetime

from ai_company.communication.bus_protocol import MessageBus  # noqa: TC001
from ai_company.communication.dispatcher import DispatchResult, MessageDispatcher
from ai_company.communication.enums import MessagePriority, MessageType
from ai_company.communication.handler import (  # noqa: TC001
    MessageHandler,
    MessageHandlerFunc,
)
from ai_company.communication.message import Message
from ai_company.communication.subscription import (  # noqa: TC001
    DeliveryEnvelope,
    Subscription,
)
from ai_company.core.types import NotBlankStr  # noqa: TC001
from ai_company.observability import get_logger
from ai_company.observability.events.communication import (
    COMM_DISPATCH_NO_DISPATCHER,
    COMM_HANDLER_DEREGISTER_MISS,
    COMM_MESSAGE_BROADCAST,
    COMM_MESSAGE_SENT,
    COMM_MESSENGER_CREATED,
    COMM_MESSENGER_INVALID_AGENT,
    COMM_MESSENGER_SUBSCRIBED,
    COMM_MESSENGER_UNSUBSCRIBED,
)

logger = get_logger(__name__)

_DIRECT_CHANNEL_PREFIX = "@"
"""Prefix for direct message channel names."""


def _direct_channel_name(agent_a: str, agent_b: str) -> str:
    """Compute the deterministic direct channel name for a pair.

    Args:
        agent_a: First agent ID.
        agent_b: Second agent ID.

    Returns:
        Channel name in ``@{sorted_a}:{sorted_b}`` format.
    """
    pair = sorted([agent_a, agent_b])
    return f"{_DIRECT_CHANNEL_PREFIX}{pair[0]}:{pair[1]}"


class AgentMessenger:
    """Per-agent facade for sending, receiving, and dispatching messages.

    Wraps a :class:`MessageBus` and optional :class:`MessageDispatcher`
    to provide a high-level API that auto-fills sender, timestamp, and
    message ID.

    Args:
        agent_id: Identifier of the owning agent.
        agent_name: Human-readable name of the agent.
        bus: The underlying message bus.
        dispatcher: Optional message dispatcher for handler routing.

    Raises:
        ValueError: If *agent_id* or *agent_name* is blank.
    """

    __slots__ = ("_agent_id", "_agent_name", "_bus", "_dispatcher")

    def __init__(
        self,
        agent_id: str,
        agent_name: str,
        bus: MessageBus,
        dispatcher: MessageDispatcher | None = None,
    ) -> None:
        if not agent_id.strip():
            logger.warning(
                COMM_MESSENGER_INVALID_AGENT,
                field="agent_id",
                value=repr(agent_id),
            )
            msg = "agent_id must not be blank"
            raise ValueError(msg)
        if not agent_name.strip():
            logger.warning(
                COMM_MESSENGER_INVALID_AGENT,
                field="agent_name",
                value=repr(agent_name),
            )
            msg = "agent_name must not be blank"
            raise ValueError(msg)
        self._agent_id = agent_id
        self._agent_name = agent_name
        self._bus = bus
        self._dispatcher = dispatcher
        logger.debug(
            COMM_MESSENGER_CREATED,
            agent_id=agent_id,
            agent_name=agent_name,
        )

    async def send_message(
        self,
        *,
        to: str,
        channel: str,
        content: str,
        message_type: MessageType,
        priority: MessagePriority = MessagePriority.NORMAL,
    ) -> Message:
        """Send a message to a channel.

        Auto-fills sender, timestamp, and message ID.

        Args:
            to: Recipient agent or channel identifier.
            channel: Channel to publish through.
            content: Message body text.
            message_type: Message type classification.
            priority: Message priority level.

        Returns:
            The constructed and published message.

        Raises:
            ChannelNotFoundError: If the channel does not exist.
            MessageBusNotRunningError: If the bus is not running.
        """
        msg = Message(
            timestamp=datetime.now(UTC),
            sender=self._agent_id,
            to=to,
            type=message_type,
            priority=priority,
            channel=channel,
            content=content,
        )
        await self._bus.publish(msg)
        logger.info(
            COMM_MESSAGE_SENT,
            agent_id=self._agent_id,
            to=to,
            channel=channel,
            message_id=str(msg.id),
        )
        return msg

    async def send_direct(
        self,
        *,
        to: str,
        content: str,
        message_type: MessageType,
        priority: MessagePriority = MessagePriority.NORMAL,
    ) -> Message:
        """Send a direct message to another agent.

        Auto-fills sender, timestamp, and message ID. The message's
        ``channel`` field is set to the deterministic ``@{a}:{b}``
        channel name computed from the sorted agent ID pair.

        Args:
            to: Recipient agent ID.
            content: Message body text.
            message_type: Message type classification.
            priority: Message priority level.

        Returns:
            The constructed and sent message.

        Raises:
            MessageBusNotRunningError: If the bus is not running.
        """
        channel = _direct_channel_name(self._agent_id, to)
        msg = Message(
            timestamp=datetime.now(UTC),
            sender=self._agent_id,
            to=to,
            type=message_type,
            priority=priority,
            channel=channel,
            content=content,
        )
        await self._bus.send_direct(msg, recipient=to)
        logger.info(
            COMM_MESSAGE_SENT,
            agent_id=self._agent_id,
            to=to,
            channel=channel,
            message_id=str(msg.id),
        )
        return msg

    async def broadcast(
        self,
        *,
        content: str,
        message_type: MessageType,
        priority: MessagePriority = MessagePriority.NORMAL,
        channel: str = "#all-hands",
    ) -> Message:
        """Publish an announcement to a shared channel.

        Agents must be subscribed to the target channel to receive
        the message.  For true fan-out to all known agents, use a
        channel of type :attr:`ChannelType.BROADCAST`.

        Args:
            content: Message body text.
            message_type: Message type classification.
            priority: Message priority level.
            channel: Channel name (default ``"#all-hands"``).

        Returns:
            The constructed and published message.

        Raises:
            ChannelNotFoundError: If the channel does not exist.
            MessageBusNotRunningError: If the bus is not running.
        """
        msg = Message(
            timestamp=datetime.now(UTC),
            sender=self._agent_id,
            to=channel,
            type=message_type,
            priority=priority,
            channel=channel,
            content=content,
        )
        await self._bus.publish(msg)
        logger.info(
            COMM_MESSAGE_BROADCAST,
            agent_id=self._agent_id,
            channel=channel,
            message_id=str(msg.id),
        )
        return msg

    async def subscribe(self, channel_name: NotBlankStr) -> Subscription:
        """Subscribe this agent to a channel.

        Args:
            channel_name: Channel to subscribe to.

        Returns:
            The subscription record.

        Raises:
            ChannelNotFoundError: If the channel does not exist.
            MessageBusNotRunningError: If the bus is not running.
        """
        sub = await self._bus.subscribe(channel_name, self._agent_id)
        logger.info(
            COMM_MESSENGER_SUBSCRIBED,
            agent_id=self._agent_id,
            channel=channel_name,
        )
        return sub

    async def unsubscribe(self, channel_name: NotBlankStr) -> None:
        """Unsubscribe this agent from a channel.

        Args:
            channel_name: Channel to unsubscribe from.

        Raises:
            NotSubscribedError: If not currently subscribed.
        """
        await self._bus.unsubscribe(channel_name, self._agent_id)
        logger.info(
            COMM_MESSENGER_UNSUBSCRIBED,
            agent_id=self._agent_id,
            channel=channel_name,
        )

    async def receive(
        self,
        channel_name: NotBlankStr,
        *,
        timeout: float | None = None,  # noqa: ASYNC109
    ) -> DeliveryEnvelope | None:
        """Receive the next message from a channel.

        Args:
            channel_name: Channel to receive from.
            timeout: Max seconds to wait, or ``None`` for indefinite.

        Returns:
            The next delivery envelope, or ``None`` when:

            - *timeout* expires without a message arriving.
            - The bus is shut down while waiting.
            - The subscription is cancelled via :meth:`unsubscribe`
              while a ``receive()`` call is in flight.
        """
        return await self._bus.receive(
            channel_name,
            self._agent_id,
            timeout=timeout,
        )

    def register_handler(
        self,
        handler: MessageHandler | MessageHandlerFunc,
        *,
        message_types: frozenset[MessageType] | None = None,
        min_priority: MessagePriority = MessagePriority.LOW,
        name: str = "unnamed",
    ) -> str:
        """Register a message handler.

        Creates a dispatcher automatically if one was not provided
        at construction time.

        Args:
            handler: The handler instance or async function.
            message_types: Message types to match (empty/None = all).
            min_priority: Minimum priority to accept.
            name: Human-readable label for debugging.

        Returns:
            The unique handler registration ID.
        """
        if self._dispatcher is None:
            self._dispatcher = MessageDispatcher(
                agent_id=self._agent_id,
            )
        return self._dispatcher.register(
            handler,
            message_types=message_types,
            min_priority=min_priority,
            name=name,
        )

    def deregister_handler(self, handler_id: str) -> bool:
        """Remove a previously registered handler.

        Args:
            handler_id: The registration ID.

        Returns:
            True if the handler was found and removed.
        """
        if self._dispatcher is None:
            logger.debug(
                COMM_HANDLER_DEREGISTER_MISS,
                agent_id=self._agent_id,
                handler_id=handler_id,
            )
            return False
        return self._dispatcher.deregister(handler_id)

    async def dispatch_message(self, message: Message) -> DispatchResult:
        """Dispatch an incoming message to registered handlers.

        Args:
            message: The message to dispatch.

        Returns:
            A :class:`DispatchResult` summarising the outcome.
        """
        if self._dispatcher is None:
            logger.debug(
                COMM_DISPATCH_NO_DISPATCHER,
                agent_id=self._agent_id,
                message_id=str(message.id),
            )
            return DispatchResult(
                message_id=message.id,
                handlers_succeeded=0,
                handlers_failed=0,
            )
        return await self._dispatcher.dispatch(message)
