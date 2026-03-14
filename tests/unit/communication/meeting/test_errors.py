"""Tests for meeting protocol error hierarchy."""

import pytest

from synthorg.communication.errors import CommunicationError
from synthorg.communication.meeting.errors import (
    MeetingAgentError,
    MeetingBudgetExhaustedError,
    MeetingError,
    MeetingParticipantError,
    MeetingProtocolNotFoundError,
)

pytestmark = pytest.mark.timeout(30)


@pytest.mark.unit
class TestMeetingErrorHierarchy:
    """Tests for meeting error hierarchy."""

    def test_meeting_error_is_communication_error(self) -> None:
        assert issubclass(MeetingError, CommunicationError)

    def test_budget_exhausted_is_meeting_error(self) -> None:
        assert issubclass(MeetingBudgetExhaustedError, MeetingError)

    def test_protocol_not_found_is_meeting_error(self) -> None:
        assert issubclass(MeetingProtocolNotFoundError, MeetingError)

    def test_participant_error_is_meeting_error(self) -> None:
        assert issubclass(MeetingParticipantError, MeetingError)

    def test_agent_error_is_meeting_error(self) -> None:
        assert issubclass(MeetingAgentError, MeetingError)


@pytest.mark.unit
class TestMeetingErrorContext:
    """Tests for error context handling."""

    def test_error_with_context(self) -> None:
        err = MeetingError(
            "something went wrong",
            context={"meeting_id": "m-1", "protocol": "round_robin"},
        )
        assert err.message == "something went wrong"
        assert err.context["meeting_id"] == "m-1"
        assert err.context["protocol"] == "round_robin"

    def test_error_without_context(self) -> None:
        err = MeetingError("bare error")
        assert err.context == {}

    def test_context_is_immutable(self) -> None:
        err = MeetingError("test", context={"key": "value"})
        with pytest.raises(TypeError):
            err.context["new_key"] = "new_value"  # type: ignore[index]

    def test_str_includes_context(self) -> None:
        err = MeetingBudgetExhaustedError(
            "budget exceeded",
            context={"meeting_id": "m-1"},
        )
        text = str(err)
        assert "budget exceeded" in text
        assert "meeting_id" in text

    def test_str_without_context(self) -> None:
        err = MeetingAgentError("agent failed")
        assert str(err) == "agent failed"

    def test_context_deep_copy(self) -> None:
        original = {"nested": {"key": "value"}}
        err = MeetingError("test", context=original)
        original["nested"]["key"] = "mutated"
        assert err.context["nested"]["key"] == "value"
