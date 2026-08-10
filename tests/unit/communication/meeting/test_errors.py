"""Tests for meeting protocol error hierarchy."""

import pytest

from synthorg.api.exception_handlers import EXCEPTION_HANDLERS
from synthorg.communication.errors import CommunicationError
from synthorg.communication.meeting.errors import (
    MeetingAgentError,
    MeetingBudgetExhaustedError,
    MeetingCeremonyRegistrationError,
    MeetingError,
    MeetingParticipantError,
    MeetingPhaseSlotError,
    MeetingProtocolNotFoundError,
    MeetingSchedulerError,
)
from synthorg.core.error_taxonomy import ErrorCategory, ErrorCode


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

    def test_phase_slot_error_is_meeting_error(self) -> None:
        # Audit 34: meeting-phase invariant violations route through a
        # domain error (COMMUNICATION_ERROR) instead of a bare RuntimeError
        # so the API surfaces a structured RFC 9457 envelope.
        assert issubclass(MeetingPhaseSlotError, MeetingError)
        assert MeetingPhaseSlotError.error_code == ErrorCode.COMMUNICATION_ERROR


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
        assert err.context["nested"]["key"] == "value"  # type: ignore[index]


@pytest.mark.unit
class TestCeremonyRegistrationIsAClientError:
    """Refusing a sprint's ceremonies is a config error, not a fault.

    It inherits from ``CommunicationError``, whose 500 default would tell
    an operator the server broke when in fact their template names
    something the meeting scheduler cannot accept and they can fix it.
    """

    def test_is_a_scheduler_error(self) -> None:
        assert issubclass(MeetingCeremonyRegistrationError, MeetingSchedulerError)

    def test_maps_to_422(self) -> None:
        assert MeetingCeremonyRegistrationError.status_code == 422

    def test_is_categorised_as_validation(self) -> None:
        assert (
            MeetingCeremonyRegistrationError.error_category is ErrorCategory.VALIDATION
        )

    def test_carries_its_own_error_code(self) -> None:
        """A shared code would make it indistinguishable to a client."""
        assert (
            MeetingCeremonyRegistrationError.error_code
            is ErrorCode.CEREMONY_REGISTRATION_INVALID
        )
        assert MeetingCeremonyRegistrationError.error_code is not ErrorCode(
            MeetingError.error_code
        )

    def test_the_handler_registers_it_above_its_parent(self) -> None:
        """Litestar walks the MRO, so ordering decides which mapping wins."""
        handled = list(EXCEPTION_HANDLERS)
        assert handled.index(MeetingCeremonyRegistrationError) < handled.index(
            CommunicationError
        )
