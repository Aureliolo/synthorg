"""Tests for AG-UI event types and StreamEvent model."""

from datetime import UTC, datetime

import pytest

from synthorg.communication.event_stream.types import AgUiEventType, StreamEvent


@pytest.mark.unit
class TestAgUiEventType:
    @pytest.mark.parametrize(
        ("member", "expected"),
        [
            (AgUiEventType.RUN_STARTED, "run_started"),
            (AgUiEventType.RUN_FINISHED, "run_finished"),
            (AgUiEventType.RUN_ERROR, "run_error"),
            (AgUiEventType.STEP_STARTED, "step_started"),
            (AgUiEventType.STEP_FINISHED, "step_finished"),
            (AgUiEventType.STEP_FAILED, "step_failed"),
            (AgUiEventType.TEXT_MESSAGE_START, "text_message_start"),
            (AgUiEventType.TEXT_MESSAGE_CONTENT, "text_message_content"),
            (AgUiEventType.TEXT_MESSAGE_END, "text_message_end"),
            (AgUiEventType.TOOL_CALL_START, "tool_call_start"),
            (AgUiEventType.TOOL_CALL_ARGS, "tool_call_args"),
            (AgUiEventType.TOOL_CALL_END, "tool_call_end"),
            (AgUiEventType.APPROVAL_INTERRUPT, "approval_interrupt"),
            (AgUiEventType.APPROVAL_RESUMED, "approval_resumed"),
            (AgUiEventType.INFO_REQUEST_INTERRUPT, "info_request_interrupt"),
            (AgUiEventType.INFO_REQUEST_RESUMED, "info_request_resumed"),
        ],
    )
    def test_member_value(self, member: AgUiEventType, expected: str) -> None:
        assert member.value == expected

    def test_all_members_count(self) -> None:
        assert len(AgUiEventType) == 16


@pytest.mark.unit
class TestStreamEvent:
    def _make_event(self, **overrides: object) -> StreamEvent:
        defaults: dict[str, object] = {
            "id": "evt-001",
            "type": AgUiEventType.RUN_STARTED,
            "timestamp": datetime(2026, 4, 13, tzinfo=UTC),
            "session_id": "session-abc",
            "correlation_id": None,
            "agent_id": None,
            "payload": {},
        }
        defaults.update(overrides)
        return StreamEvent(**defaults)  # type: ignore[arg-type]

    def test_construction(self) -> None:
        event = self._make_event()
        assert event.id == "evt-001"
        assert event.type == AgUiEventType.RUN_STARTED
        assert event.session_id == "session-abc"

    def test_frozen(self) -> None:
        event = self._make_event()
        with pytest.raises(Exception, match="frozen"):
            event.id = "changed"  # type: ignore[misc]

    def test_payload_deep_copied(self) -> None:
        original: dict[str, object] = {"key": "value", "nested": {"a": 1}}
        event = self._make_event(payload=original)
        original["key"] = "mutated"
        original["nested"]["a"] = 99  # type: ignore[index]
        assert event.payload["key"] == "value"
        assert event.payload["nested"]["a"] == 1  # type: ignore[index]

    def test_optional_fields(self) -> None:
        event = self._make_event(
            correlation_id="corr-123",
            agent_id="agent-xyz",
        )
        assert event.correlation_id == "corr-123"
        assert event.agent_id == "agent-xyz"

    def test_blank_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            self._make_event(id="")

    def test_blank_session_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="at least 1"):
            self._make_event(session_id="")

    def test_whitespace_id_rejected(self) -> None:
        with pytest.raises(ValueError, match="whitespace"):
            self._make_event(id="   ")
