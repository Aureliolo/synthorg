"""Tests for WebSocket event models."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from synthorg.api.ws_models import WsEvent, WsEventType

_TASK_CREATED_PAYLOAD: dict[str, object] = {
    "task_id": "task-001",
    "title": "Build feature",
    "status": "PENDING",
}

_BUDGET_ALERT_PAYLOAD: dict[str, object] = {
    "severity": "warning",
    "message": "monthly spend at 80%",
    "currency": "USD",
}

_MESSAGE_SENT_PAYLOAD: dict[str, object] = {
    "message_id": "msg-1",
    "sender": "agent-a",
    "to": "agent-b",
    "content": "hello",
    "parts": [],
}


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
            payload=_TASK_CREATED_PAYLOAD,
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
            payload=_BUDGET_ALERT_PAYLOAD,
        )
        json_str = event.model_dump_json()
        restored = WsEvent.model_validate_json(json_str)
        assert restored.event_type == WsEventType.BUDGET_ALERT
        assert restored.channel == "budget"
        assert restored.payload == _BUDGET_ALERT_PAYLOAD

    def test_ws_event_frozen(self) -> None:
        event = WsEvent(
            event_type=WsEventType.MESSAGE_SENT,
            channel="messages",
            timestamp=datetime(2026, 3, 1, tzinfo=UTC),
            payload=_MESSAGE_SENT_PAYLOAD,
        )
        with pytest.raises(ValidationError):
            event.channel = "other"  # type: ignore[misc]

    def test_ws_event_version_defaults_to_one(self) -> None:
        event = WsEvent(
            event_type=WsEventType.TASK_CREATED,
            channel="tasks",
            timestamp=datetime(2026, 3, 1, tzinfo=UTC),
            payload=_TASK_CREATED_PAYLOAD,
        )
        assert event.version == 1

    def test_ws_event_version_round_trips_in_json(self) -> None:
        event = WsEvent(
            event_type=WsEventType.TASK_CREATED,
            channel="tasks",
            timestamp=datetime(2026, 3, 1, tzinfo=UTC),
            payload=_TASK_CREATED_PAYLOAD,
        )
        restored = WsEvent.model_validate_json(event.model_dump_json())
        assert restored.version == 1

    def test_ws_event_explicit_version_accepted(self) -> None:
        event = WsEvent(
            event_type=WsEventType.TASK_CREATED,
            channel="tasks",
            timestamp=datetime(2026, 3, 1, tzinfo=UTC),
            payload=_TASK_CREATED_PAYLOAD,
            version=2,
        )
        assert event.version == 2

    def test_ws_event_version_zero_rejected(self) -> None:
        with pytest.raises(ValidationError):
            WsEvent(
                event_type=WsEventType.TASK_CREATED,
                channel="tasks",
                timestamp=datetime(2026, 3, 1, tzinfo=UTC),
                payload=_TASK_CREATED_PAYLOAD,
                version=0,
            )

    def test_ws_event_payload_shape_validated(self) -> None:
        """Constructing with a payload that doesn't match the typed model fails."""
        with pytest.raises(ValidationError):
            WsEvent(
                event_type=WsEventType.TASK_CREATED,
                channel="tasks",
                timestamp=datetime(2026, 3, 1, tzinfo=UTC),
                payload={"task_id": "task-001"},  # missing title + status
            )

    def test_ws_event_payload_extra_fields_rejected(self) -> None:
        """Extra fields outside the payload model schema are rejected."""
        with pytest.raises(ValidationError):
            WsEvent(
                event_type=WsEventType.TASK_CREATED,
                channel="tasks",
                timestamp=datetime(2026, 3, 1, tzinfo=UTC),
                payload={**_TASK_CREATED_PAYLOAD, "unknown_field": "value"},
            )
