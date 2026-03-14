"""Tests for WebSocket event models."""

from datetime import UTC, datetime

import pytest

from synthorg.api.ws_models import WsEvent, WsEventType


@pytest.mark.unit
class TestWsModels:
    def test_event_type_values(self) -> None:
        assert WsEventType.TASK_CREATED.value == "task.created"
        assert WsEventType.SYSTEM_SHUTDOWN.value == "system.shutdown"

    def test_ws_event_serialization(self) -> None:
        event = WsEvent(
            event_type=WsEventType.TASK_CREATED,
            channel="tasks",
            timestamp=datetime(2026, 3, 1, tzinfo=UTC),
            payload={"task_id": "task-001"},
        )
        data = event.model_dump()
        assert data["event_type"] == "task.created"
        assert data["channel"] == "tasks"
        assert data["payload"]["task_id"] == "task-001"

    def test_ws_event_json_roundtrip(self) -> None:
        event = WsEvent(
            event_type=WsEventType.BUDGET_ALERT,
            channel="budget",
            timestamp=datetime(2026, 3, 1, tzinfo=UTC),
        )
        json_str = event.model_dump_json()
        restored = WsEvent.model_validate_json(json_str)
        assert restored.event_type == WsEventType.BUDGET_ALERT
        assert restored.channel == "budget"
        assert restored.payload == {}

    def test_ws_event_frozen(self) -> None:
        from pydantic import ValidationError

        event = WsEvent(
            event_type=WsEventType.MESSAGE_SENT,
            channel="messages",
            timestamp=datetime(2026, 3, 1, tzinfo=UTC),
        )
        with pytest.raises(ValidationError):
            event.channel = "other"  # type: ignore[misc]
