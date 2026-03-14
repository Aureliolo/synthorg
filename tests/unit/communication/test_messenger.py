"""Unit tests for AgentMessenger."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from synthorg.communication.bus_protocol import MessageBus
from synthorg.communication.dispatcher import MessageDispatcher
from synthorg.communication.enums import (
    MessagePriority,
    MessageType,
)
from synthorg.communication.errors import ChannelNotFoundError
from synthorg.communication.message import Message
from synthorg.communication.messenger import AgentMessenger
from synthorg.communication.subscription import Subscription

pytestmark = pytest.mark.timeout(30)


def _make_mock_bus() -> MagicMock:
    """Create a mock MessageBus with all async methods."""
    bus = MagicMock(spec=MessageBus)
    bus.publish = AsyncMock()
    bus.send_direct = AsyncMock()
    bus.subscribe = AsyncMock(
        return_value=Subscription(
            channel_name="#test",
            subscriber_id="agent-a",
            subscribed_at=datetime.now(UTC),
        ),
    )
    bus.unsubscribe = AsyncMock()
    return bus


class TestAgentMessengerInit:
    """Tests for AgentMessenger.__init__ validation."""

    @pytest.mark.unit
    def test_blank_agent_id_rejected(self) -> None:
        bus = _make_mock_bus()
        with pytest.raises(ValueError, match="agent_id must not be blank"):
            AgentMessenger(
                agent_id="  ",
                agent_name="Agent A",
                bus=bus,
            )

    @pytest.mark.unit
    def test_blank_agent_name_rejected(self) -> None:
        bus = _make_mock_bus()
        with pytest.raises(ValueError, match="agent_name must not be blank"):
            AgentMessenger(
                agent_id="agent-a",
                agent_name="  ",
                bus=bus,
            )

    @pytest.mark.unit
    def test_empty_agent_id_rejected(self) -> None:
        bus = _make_mock_bus()
        with pytest.raises(ValueError, match="agent_id must not be blank"):
            AgentMessenger(
                agent_id="",
                agent_name="Agent A",
                bus=bus,
            )

    @pytest.mark.unit
    def test_empty_agent_name_rejected(self) -> None:
        bus = _make_mock_bus()
        with pytest.raises(ValueError, match="agent_name must not be blank"):
            AgentMessenger(
                agent_id="agent-a",
                agent_name="",
                bus=bus,
            )


class TestSendMessage:
    """Tests for AgentMessenger.send_message."""

    @pytest.mark.unit
    async def test_send_message_auto_fills_sender(self) -> None:
        bus = _make_mock_bus()
        messenger = AgentMessenger(
            agent_id="agent-a",
            agent_name="Agent A",
            bus=bus,
        )

        msg = await messenger.send_message(
            to="agent-b",
            channel="#eng",
            content="hello",
            message_type=MessageType.TASK_UPDATE,
        )

        assert msg.sender == "agent-a"
        bus.publish.assert_awaited_once()

    @pytest.mark.unit
    async def test_send_message_auto_fills_timestamp(self) -> None:
        bus = _make_mock_bus()
        messenger = AgentMessenger(
            agent_id="agent-a",
            agent_name="Agent A",
            bus=bus,
        )

        before = datetime.now(UTC)
        msg = await messenger.send_message(
            to="agent-b",
            channel="#eng",
            content="hello",
            message_type=MessageType.QUESTION,
        )
        after = datetime.now(UTC)

        assert before <= msg.timestamp <= after

    @pytest.mark.unit
    async def test_send_message_auto_fills_id(self) -> None:
        bus = _make_mock_bus()
        messenger = AgentMessenger(
            agent_id="agent-a",
            agent_name="Agent A",
            bus=bus,
        )

        msg = await messenger.send_message(
            to="agent-b",
            channel="#eng",
            content="hello",
            message_type=MessageType.ANNOUNCEMENT,
        )

        assert msg.id is not None

    @pytest.mark.unit
    async def test_send_message_passes_priority(self) -> None:
        bus = _make_mock_bus()
        messenger = AgentMessenger(
            agent_id="agent-a",
            agent_name="Agent A",
            bus=bus,
        )

        msg = await messenger.send_message(
            to="agent-b",
            channel="#eng",
            content="urgent matter",
            message_type=MessageType.ESCALATION,
            priority=MessagePriority.URGENT,
        )

        assert msg.priority == MessagePriority.URGENT

    @pytest.mark.unit
    async def test_send_message_calls_bus_publish(self) -> None:
        bus = _make_mock_bus()
        messenger = AgentMessenger(
            agent_id="agent-a",
            agent_name="Agent A",
            bus=bus,
        )

        msg = await messenger.send_message(
            to="agent-b",
            channel="#eng",
            content="hello",
            message_type=MessageType.TASK_UPDATE,
        )

        bus.publish.assert_awaited_once_with(msg)

    @pytest.mark.unit
    async def test_send_message_propagates_bus_error(self) -> None:
        bus = _make_mock_bus()
        bus.publish = AsyncMock(
            side_effect=ChannelNotFoundError(
                "not found",
                context={"channel": "#missing"},
            ),
        )
        messenger = AgentMessenger(
            agent_id="agent-a",
            agent_name="Agent A",
            bus=bus,
        )

        with pytest.raises(ChannelNotFoundError):
            await messenger.send_message(
                to="agent-b",
                channel="#missing",
                content="hello",
                message_type=MessageType.TASK_UPDATE,
            )


class TestSendDirect:
    """Tests for AgentMessenger.send_direct."""

    @pytest.mark.unit
    async def test_send_direct_calls_bus(self) -> None:
        bus = _make_mock_bus()
        messenger = AgentMessenger(
            agent_id="agent-a",
            agent_name="Agent A",
            bus=bus,
        )

        msg = await messenger.send_direct(
            to="agent-b",
            content="dm",
            message_type=MessageType.QUESTION,
        )

        assert msg.sender == "agent-a"
        bus.send_direct.assert_awaited_once_with(msg, recipient="agent-b")

    @pytest.mark.unit
    async def test_send_direct_uses_direct_channel_placeholder(
        self,
    ) -> None:
        bus = _make_mock_bus()
        messenger = AgentMessenger(
            agent_id="agent-a",
            agent_name="Agent A",
            bus=bus,
        )

        msg = await messenger.send_direct(
            to="agent-b",
            content="dm",
            message_type=MessageType.TASK_UPDATE,
        )

        # Channel is set to the deterministic direct channel name
        assert msg.channel == "@agent-a:agent-b"


class TestBroadcast:
    """Tests for AgentMessenger.broadcast."""

    @pytest.mark.unit
    async def test_broadcast_default_channel(self) -> None:
        bus = _make_mock_bus()
        messenger = AgentMessenger(
            agent_id="agent-a",
            agent_name="Agent A",
            bus=bus,
        )

        msg = await messenger.broadcast(
            content="all hands",
            message_type=MessageType.ANNOUNCEMENT,
        )

        assert msg.channel == "#all-hands"
        bus.publish.assert_awaited_once_with(msg)

    @pytest.mark.unit
    async def test_broadcast_custom_channel(self) -> None:
        bus = _make_mock_bus()
        messenger = AgentMessenger(
            agent_id="agent-a",
            agent_name="Agent A",
            bus=bus,
        )

        msg = await messenger.broadcast(
            content="eng only",
            message_type=MessageType.ANNOUNCEMENT,
            channel="#engineering",
        )

        assert msg.channel == "#engineering"

    @pytest.mark.unit
    async def test_broadcast_to_field_is_broadcast(self) -> None:
        bus = _make_mock_bus()
        messenger = AgentMessenger(
            agent_id="agent-a",
            agent_name="Agent A",
            bus=bus,
        )

        msg = await messenger.broadcast(
            content="hello",
            message_type=MessageType.ANNOUNCEMENT,
        )

        assert msg.to == "#all-hands"


class TestSubscription:
    """Tests for subscribe/unsubscribe delegation."""

    @pytest.mark.unit
    async def test_subscribe_delegates_to_bus(self) -> None:
        bus = _make_mock_bus()
        messenger = AgentMessenger(
            agent_id="agent-a",
            agent_name="Agent A",
            bus=bus,
        )

        sub = await messenger.subscribe("#eng")

        bus.subscribe.assert_awaited_once_with("#eng", "agent-a")
        assert isinstance(sub, Subscription)

    @pytest.mark.unit
    async def test_unsubscribe_delegates_to_bus(self) -> None:
        bus = _make_mock_bus()
        messenger = AgentMessenger(
            agent_id="agent-a",
            agent_name="Agent A",
            bus=bus,
        )

        await messenger.unsubscribe("#eng")

        bus.unsubscribe.assert_awaited_once_with("#eng", "agent-a")

    @pytest.mark.unit
    async def test_receive_delegates_to_bus(self) -> None:
        bus = _make_mock_bus()
        bus.receive = AsyncMock(return_value=None)
        messenger = AgentMessenger(
            agent_id="agent-a",
            agent_name="Agent A",
            bus=bus,
        )

        result = await messenger.receive("#eng", timeout=5.0)

        bus.receive.assert_awaited_once_with("#eng", "agent-a", timeout=5.0)
        assert result is None


class TestHandlerDelegation:
    """Tests for handler registration/dispatch delegation."""

    @pytest.mark.unit
    async def test_register_handler_delegates_to_dispatcher(self) -> None:
        bus = _make_mock_bus()
        dispatcher = MessageDispatcher(agent_id="agent-a")
        messenger = AgentMessenger(
            agent_id="agent-a",
            agent_name="Agent A",
            bus=bus,
            dispatcher=dispatcher,
        )

        async def handler(msg: Message) -> None:
            pass

        handler_id = messenger.register_handler(
            handler,
            name="test-handler",
        )

        assert isinstance(handler_id, str)

    @pytest.mark.unit
    async def test_deregister_handler_delegates(self) -> None:
        bus = _make_mock_bus()
        dispatcher = MessageDispatcher(agent_id="agent-a")
        messenger = AgentMessenger(
            agent_id="agent-a",
            agent_name="Agent A",
            bus=bus,
            dispatcher=dispatcher,
        )

        async def handler(msg: Message) -> None:
            pass

        handler_id = messenger.register_handler(handler, name="h1")
        result = messenger.deregister_handler(handler_id)

        assert result is True

    @pytest.mark.unit
    async def test_deregister_unknown_returns_false(self) -> None:
        bus = _make_mock_bus()
        dispatcher = MessageDispatcher(agent_id="agent-a")
        messenger = AgentMessenger(
            agent_id="agent-a",
            agent_name="Agent A",
            bus=bus,
            dispatcher=dispatcher,
        )

        result = messenger.deregister_handler("nonexistent")

        assert result is False

    @pytest.mark.unit
    async def test_dispatch_message_delegates(self) -> None:
        bus = _make_mock_bus()
        dispatcher = MessageDispatcher(agent_id="agent-a")
        messenger = AgentMessenger(
            agent_id="agent-a",
            agent_name="Agent A",
            bus=bus,
            dispatcher=dispatcher,
        )

        received: list[Message] = []

        async def handler(msg: Message) -> None:
            received.append(msg)

        messenger.register_handler(handler, name="catcher")

        msg = Message(
            timestamp=datetime.now(UTC),
            sender="agent-b",
            to="agent-a",
            type=MessageType.TASK_UPDATE,
            channel="#eng",
            content="test",
        )

        result = await messenger.dispatch_message(msg)

        assert result.handlers_matched == 1
        assert result.handlers_succeeded == 1
        assert len(received) == 1

    @pytest.mark.unit
    async def test_dispatch_without_dispatcher_returns_empty(
        self,
    ) -> None:
        bus = _make_mock_bus()
        messenger = AgentMessenger(
            agent_id="agent-a",
            agent_name="Agent A",
            bus=bus,
        )

        msg = Message(
            timestamp=datetime.now(UTC),
            sender="agent-b",
            to="agent-a",
            type=MessageType.TASK_UPDATE,
            channel="#eng",
            content="test",
        )

        result = await messenger.dispatch_message(msg)

        assert result.handlers_matched == 0
        assert result.handlers_succeeded == 0

    @pytest.mark.unit
    async def test_register_handler_without_dispatcher_creates_one(
        self,
    ) -> None:
        bus = _make_mock_bus()
        messenger = AgentMessenger(
            agent_id="agent-a",
            agent_name="Agent A",
            bus=bus,
        )

        async def handler(msg: Message) -> None:
            pass

        handler_id = messenger.register_handler(handler, name="auto")

        assert isinstance(handler_id, str)
        # Dispatcher was auto-created, so dispatch should work
        msg = Message(
            timestamp=datetime.now(UTC),
            sender="agent-b",
            to="agent-a",
            type=MessageType.TASK_UPDATE,
            channel="#eng",
            content="test",
        )
        result = await messenger.dispatch_message(msg)
        assert result.handlers_matched == 1

    @pytest.mark.unit
    async def test_deregister_handler_without_dispatcher_returns_false(
        self,
    ) -> None:
        """Deregistering when no dispatcher exists returns False."""
        bus = _make_mock_bus()
        messenger = AgentMessenger(
            agent_id="agent-a",
            agent_name="Agent A",
            bus=bus,
        )

        result = messenger.deregister_handler("nonexistent-id")

        assert result is False
