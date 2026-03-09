"""Tests for message bus bridge."""

from datetime import UTC, datetime

import pytest

from ai_company.api.bus_bridge import MessageBusBridge
from ai_company.api.ws_models import WsEventType
from ai_company.communication.enums import MessagePriority, MessageType
from ai_company.communication.message import Message


@pytest.mark.unit
class TestMessageBusBridge:
    def test_to_ws_event_conversion(self) -> None:
        msg = Message.model_validate(
            {
                "from": "alice",
                "to": "bob",
                "channel": "general",
                "content": "Hello!",
                "type": MessageType.TASK_UPDATE,
                "priority": MessagePriority.NORMAL,
                "timestamp": datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC),
            }
        )
        event = MessageBusBridge._to_ws_event(msg, "messages")
        assert event.event_type == WsEventType.MESSAGE_SENT
        assert event.channel == "messages"
        assert event.payload["sender"] == "alice"
        assert event.payload["content"] == "Hello!"

    def test_to_ws_event_has_timestamp(self) -> None:
        msg = Message.model_validate(
            {
                "from": "alice",
                "to": "bob",
                "channel": "general",
                "content": "Test",
                "type": MessageType.TASK_UPDATE,
                "priority": MessagePriority.NORMAL,
                "timestamp": datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC),
            }
        )
        event = MessageBusBridge._to_ws_event(msg, "tasks")
        assert event.timestamp is not None
